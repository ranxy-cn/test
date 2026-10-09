// DevOpsAgent 子机采集探针（Go 实现，静态编译单二进制，零运行时依赖）。
//
// 功能：按配置间隔采集 /proc（CPU/内存/磁盘/负载/网络速率），并按生效告警策略
// 采集扩展指标（swap/inode/磁盘IO延迟/TCP连接/OOM/关键进程/端口探活/网络丢包延迟/
// 带宽占比/Prometheus 指标抓取），推送平台 /api/v1/agent/report；
// 断网本地环形缓存补发；定期同步平台配置与告警策略（策略变更无需重启 agent）。
//
// 构建：make build-go   （产出 dist/agent-linux-amd64）
// 运行：./agent --config config.json
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const agentVersion = "1.1.0-go"
const procDir = "/proc"

var skipIfacePrefixes = []string{"lo", "docker", "veth", "br-", "virbr", "tun", "tap", "flannel", "cni", "cali"}

// ---------- 配置 ----------

type Config struct {
	Server      string   `json:"server"`
	Token       string   `json:"token"`
	AssetID     string   `json:"asset_id"`
	AgentConfig AgentCfg `json:"agent_config"`
}

type AgentCfg struct {
	ReportInterval  int      `json:"report_interval"`
	CollectInterval int      `json:"collect_interval"`
	CollectItems    []string `json:"collect_items"`
	BufferMax       int      `json:"buffer_max"`
	ConfigRefresh   int      `json:"config_refresh"`
	OfflineAfter    int      `json:"offline_after"`
}

// AlertPolicy 生效告警策略（平台下发的 v2 策略，agent 只读采集相关字段）。
type AlertPolicy struct {
	SwapEnabled      bool     `json:"swap_enabled"`
	InodeEnabled     bool     `json:"inode_enabled"`
	DiskIOEnabled    bool     `json:"disk_io_enabled"`
	NetPerfEnabled   bool     `json:"net_perf_enabled"`
	NetProbeTarget   string   `json:"net_probe_target"`
	BandwidthEnabled bool     `json:"bandwidth_enabled"`
	TcpConnEnabled   bool     `json:"tcp_conn_enabled"`
	OomEnabled       bool     `json:"oom_enabled"`
	ProcessEnabled   bool     `json:"process_enabled"`
	PortEnabled      bool     `json:"port_enabled"`
	MetricsEnabled   bool     `json:"metrics_scrape_enabled"`
	ProcessItems     []string `json:"process_items"`
	PortItems        []int    `json:"port_items"`
	MetricsUrls      []string `json:"metrics_urls"`
}

func defaults() AgentCfg {
	return AgentCfg{
		ReportInterval: 5, CollectInterval: 5,
		CollectItems: []string{"cpu", "mem", "disk", "load", "net"},
		BufferMax:    600, ConfigRefresh: 300, OfflineAfter: 30,
	}
}

func loadConfig(path string) Config {
	cfg := Config{AgentConfig: defaults()}
	data, err := os.ReadFile(path)
	if err == nil {
		_ = json.Unmarshal(data, &cfg)
	}
	if cfg.AgentConfig.ReportInterval <= 0 {
		cfg.AgentConfig = defaults()
	}
	return cfg
}

// ---------- /proc 基础采集 ----------

func readFile(name string) string {
	data, err := os.ReadFile(filepath.Join(procDir, name))
	if err != nil {
		return ""
	}
	return string(data)
}

func primaryIface() string {
	for _, line := range strings.Split(readFile("net/route"), "\n")[1:] {
		cols := strings.Fields(line)
		if len(cols) > 7 && cols[1] == "00000000" {
			return cols[0]
		}
	}
	for _, line := range strings.Split(readFile("net/dev"), "\n")[2:] {
		if idx := strings.Index(line, ":"); idx > 0 {
			name := strings.TrimSpace(line[:idx])
			skip := false
			for _, p := range skipIfacePrefixes {
				if strings.HasPrefix(name, p) {
					skip = true
					break
				}
			}
			if !skip {
				return name
			}
		}
	}
	return "eth0"
}

type snapshot struct {
	total, idle float64
	netTS       time.Time
	rx, tx      float64
	hasCPU, net bool
	// diskstats 差分：io_ticks(ms) / io_ops
	ioTicks, ioOps float64
	hasIO          bool
}

var (
	mu   sync.Mutex
	prev snapshot
)

func round2(v float64) float64 { return math2(v, 2) }
func math2(v float64, n int) float64 {
	p := 1.0
	for i := 0; i < n; i++ {
		p *= 10
	}
	return float64(int64(v*p+0.5)) / p
}

func cpuPct() (float64, bool) {
	line := strings.SplitN(readFile("stat"), "\n", 2)[0]
	fields := strings.Fields(line)[1:]
	if len(fields) < 4 {
		return 0, false
	}
	var vals [8]float64
	total := 0.0
	for i := 0; i < len(fields) && i < 8; i++ {
		vals[i], _ = strconv.ParseFloat(fields[i], 64)
		total += vals[i]
	}
	idle := vals[3] + vals[4]
	mu.Lock()
	defer mu.Unlock()
	old := prev
	prev.total, prev.idle, prev.hasCPU = total, idle, true
	if !old.hasCPU || total-old.total <= 0 {
		return 0, false
	}
	pct := (1 - (idle-old.idle)/(total-old.total)) * 100
	if pct < 0 {
		pct = 0
	}
	if pct > 100 {
		pct = 100
	}
	return round2(pct), true
}

func memPct() (float64, bool) {
	total, avail := 0.0, -1.0
	for _, line := range strings.Split(readFile("meminfo"), "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		kb, err := strconv.ParseFloat(strings.TrimSpace(strings.Fields(parts[1])[0]), 64)
		if err != nil {
			continue
		}
		switch strings.TrimSpace(parts[0]) {
		case "MemTotal":
			total = kb
		case "MemAvailable":
			avail = kb
		}
	}
	if total <= 0 || avail < 0 {
		return 0, false
	}
	return round2((total - avail) / total * 100), true
}

func diskPct() (float64, bool) {
	var st syscall.Statfs_t
	if err := syscall.Statfs("/", &st); err != nil {
		return 0, false
	}
	total := float64(st.Blocks) * float64(st.Bsize)
	free := float64(st.Bavail) * float64(st.Bsize)
	if total <= 0 {
		return 0, false
	}
	return round2((total - free) / total * 100), true
}

func load1() (float64, bool) {
	fields := strings.Fields(readFile("loadavg"))
	if len(fields) == 0 {
		return 0, false
	}
	v, err := strconv.ParseFloat(fields[0], 64)
	if err != nil {
		return 0, false
	}
	return round2(v), true
}

func netRates() (rx, tx float64, ok bool) {
	iface := primaryIface()
	for _, line := range strings.Split(readFile("net/dev"), "\n")[2:] {
		idx := strings.Index(line, ":")
		if idx < 0 || strings.TrimSpace(line[:idx]) != iface {
			continue
		}
		fields := strings.Fields(line[idx+1:])
		if len(fields) < 9 {
			return 0, 0, false
		}
		rx, _ = strconv.ParseFloat(fields[0], 64)
		tx, _ = strconv.ParseFloat(fields[8], 64)
		now := time.Now()
		mu.Lock()
		old := prev
		prev.netTS, prev.rx, prev.tx, prev.net = now, rx, tx, true
		mu.Unlock()
		if !old.net {
			return 0, 0, false
		}
		dt := now.Sub(old.netTS).Seconds()
		if dt <= 0 {
			return 0, 0, false
		}
		drx, dtx := rx-old.rx, tx-old.tx
		if drx < 0 {
			drx = 0
		}
		if dtx < 0 {
			dtx = 0
		}
		return math2(drx/dt, 1), math2(dtx/dt, 1), true
	}
	return 0, 0, false
}

// ---------- 进程归因（告警窗口样本附带进程名 + PID） ----------

// topProcs：单次扫描 /proc，计算每个进程的平均 CPU%（自进程启动）与内存%，
// 取 CPU TOP5 与内存 TOP5 的并集（按 pid 去重，最多 10 条），输出 [{pid, comm, cpu, mem}]。
// cpu = (utime+stime)/USER_HZ / elapsed * 100；elapsed = uptime - starttime/USER_HZ。
func topProcs() []map[string]interface{} {
	upSec, ok := uptimeSec()
	if !ok {
		return nil
	}
	pageSize := float64(os.Getpagesize())
	const hz = 100.0 // Linux USER_HZ
	totalKB := memTotalKB()
	type pinfo struct {
		pid                     int64
		ppid                    int64
		comm, user, args, etime string
		cpu, mem                float64
	}
	var procs []pinfo
	entries, err := os.ReadDir(procDir)
	if err != nil {
		return nil
	}
	for _, e := range entries {
		name := e.Name()
		if name[0] < '0' || name[0] > '9' {
			continue
		}
		data := readFile(name + "/stat")
		l, r := strings.Index(data, "("), strings.LastIndex(data, ")")
		if l < 0 || r < l {
			continue
		}
		pid, _ := strconv.ParseInt(name, 10, 64)
		comm := data[l+1 : r]
		rest := strings.Fields(data[r+2:]) // 从 state（第 3 字段）开始
		if len(rest) < 20 {
			continue
		}
		utime, _ := strconv.ParseFloat(rest[11], 64) // 第 14 字段
		stime, _ := strconv.ParseFloat(rest[12], 64) // 第 15 字段
		start, _ := strconv.ParseFloat(rest[19], 64) // 第 22 字段
		ppid, _ := strconv.ParseInt(rest[1], 10, 64) // 第 4 字段
		cpu := 0.0
		etime := ""
		if elapsed := upSec - start/hz; elapsed > 0 {
			cpu = (utime + stime) / hz / elapsed * 100
			etime = fmtElapsed(elapsed)
		}
		mem := 0.0
		if st := strings.Fields(readFile(name + "/statm")); len(st) > 1 && totalKB > 0 {
			rss, _ := strconv.ParseFloat(st[1], 64)
			mem = rss * pageSize / 1024 / totalKB * 100
		}
		procs = append(procs, pinfo{pid: pid, ppid: ppid, comm: comm, user: procUser(name), args: procArgs(name), etime: etime, cpu: round2(cpu), mem: round2(mem)})
	}
	byCpu := append([]pinfo(nil), procs...)
	sort.Slice(byCpu, func(i, j int) bool { return byCpu[i].cpu > byCpu[j].cpu })
	if len(byCpu) > 5 {
		byCpu = byCpu[:5]
	}
	byMem := append([]pinfo(nil), procs...)
	sort.Slice(byMem, func(i, j int) bool { return byMem[i].mem > byMem[j].mem })
	if len(byMem) > 5 {
		byMem = byMem[:5]
	}
	seen := map[int64]bool{}
	out := make([]map[string]interface{}, 0, 10)
	for _, p := range byCpu {
		seen[p.pid] = true
		out = append(out, map[string]interface{}{"pid": p.pid, "ppid": p.ppid, "comm": p.comm, "user": p.user, "args": p.args, "etime": p.etime, "cpu": p.cpu, "mem": p.mem})
	}
	for _, p := range byMem {
		if len(out) >= 10 || seen[p.pid] {
			continue
		}
		seen[p.pid] = true
		out = append(out, map[string]interface{}{"pid": p.pid, "ppid": p.ppid, "comm": p.comm, "user": p.user, "args": p.args, "etime": p.etime, "cpu": p.cpu, "mem": p.mem})
	}
	return out
}

// fmtElapsed：秒 → ps 风格运行时长（[[dd-]hh:]mm:ss）。
func fmtElapsed(sec float64) string {
	s := int64(sec)
	d := s / 86400
	h := (s % 86400) / 3600
	m := (s % 3600) / 60
	secPart := s % 60
	if d > 0 {
		return fmt.Sprintf("%dd %02d:%02d:%02d", d, h, m, secPart)
	}
	if h > 0 {
		return fmt.Sprintf("%02d:%02d:%02d", h, m, secPart)
	}
	return fmt.Sprintf("%02d:%02d", m, secPart)
}

// ---- 进程用户/命令辅助（为 topProcs 补 user/args，替代 SSH ps） ----

var (
	passwdMu  sync.Mutex
	passwdMap map[string]string
)

func userOfUID(uid string) string {
	passwdMu.Lock()
	defer passwdMu.Unlock()
	if passwdMap == nil {
		passwdMap = map[string]string{}
		if data, err := os.ReadFile("/etc/passwd"); err == nil {
			for _, line := range strings.Split(string(data), "\n") {
				parts := strings.Split(line, ":")
				if len(parts) >= 3 {
					passwdMap[parts[2]] = parts[0]
				}
			}
		}
	}
	if u, ok := passwdMap[uid]; ok {
		return u
	}
	return uid
}

// procUser：读 /proc/<pid>/status 的 Uid 行（real uid）映射用户名。
func procUser(pidDir string) string {
	for _, line := range strings.Split(readFile(filepath.Join(pidDir, "status")), "\n") {
		if strings.HasPrefix(line, "Uid:") {
			f := strings.Fields(line)
			if len(f) >= 2 {
				return userOfUID(f[1])
			}
			break
		}
	}
	return ""
}

// procArgs：读 /proc/<pid>/cmdline，NUL 替换为空格。
func procArgs(pidDir string) string {
	// 必须经 readFile（自动补 /proc 前缀）：pidDir 是相对名，agent CWD 不在 /proc 下
	data := readFile(filepath.Join(pidDir, "cmdline"))
	if data == "" {
		return ""
	}
	return strings.TrimSpace(strings.ReplaceAll(data, "\x00", " "))
}

// loadAvg：/proc/loadavg 前三字段 + 逻辑核数（runtime.NumCPU；
// 注意第 4 字段 "running/total" 的 total 是线程总数，不是核数）。
func loadAvg() (l1, l5, l15 float64, ncpu int, ok bool) {
	fields := strings.Fields(readFile("loadavg"))
	if len(fields) < 3 {
		return 0, 0, 0, 0, false
	}
	a, e1 := strconv.ParseFloat(fields[0], 64)
	b, e2 := strconv.ParseFloat(fields[1], 64)
	c, e3 := strconv.ParseFloat(fields[2], 64)
	if e1 != nil || e2 != nil || e3 != nil {
		return 0, 0, 0, 0, false
	}
	return round2(a), round2(b), round2(c), runtime.NumCPU(), true
}

// memKB：/proc/meminfo 的 MemTotal 与 used(=Total-Available)，单位 KB。
func memKB() (total, used float64, ok bool) {
	total, avail := 0.0, -1.0
	for _, line := range strings.Split(readFile("meminfo"), "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		kb, err := strconv.ParseFloat(strings.TrimSpace(strings.Fields(parts[1])[0]), 64)
		if err != nil {
			continue
		}
		switch strings.TrimSpace(parts[0]) {
		case "MemTotal":
			total = kb
		case "MemAvailable":
			avail = kb
		}
	}
	if total <= 0 || avail < 0 {
		return 0, 0, false
	}
	if used = total - avail; used < 0 {
		used = 0
	}
	return total, used, true
}

func uptimeSec() (float64, bool) {
	fields := strings.Fields(readFile("uptime"))
	if len(fields) == 0 {
		return 0, false
	}
	v, err := strconv.ParseFloat(fields[0], 64)
	if err != nil || v <= 0 {
		return 0, false
	}
	return v, true
}

func memTotalKB() float64 {
	for _, line := range strings.Split(readFile("meminfo"), "\n") {
		if strings.HasPrefix(line, "MemTotal:") {
			v, err := strconv.ParseFloat(strings.TrimSpace(strings.Fields(strings.SplitN(line, ":", 2)[1])[0]), 64)
			if err == nil {
				return v
			}
		}
	}
	return 0
}

// ---------- v1.1 扩展采集（按告警策略开关） ----------

// swapPct：Swap 使用率（meminfo）。
func swapPct() (float64, bool) {
	total, free := 0.0, -1.0
	for _, line := range strings.Split(readFile("meminfo"), "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		kb, err := strconv.ParseFloat(strings.TrimSpace(strings.Fields(parts[1])[0]), 64)
		if err != nil {
			continue
		}
		switch strings.TrimSpace(parts[0]) {
		case "SwapTotal":
			total = kb
		case "SwapFree":
			free = kb
		}
	}
	if total <= 0 || free < 0 {
		return 0, false // 未启用 swap 不上报
	}
	return round2((total - free) / total * 100), true
}

// inodePct：根分区 inode 使用率。
func inodePct() (float64, bool) {
	var st syscall.Statfs_t
	if err := syscall.Statfs("/", &st); err != nil {
		return 0, false
	}
	if st.Files <= 0 {
		return 0, false
	}
	return round2(float64(st.Files-st.Ffree) / float64(st.Files) * 100), true
}

// diskAwait：磁盘 IO 平均等待时间（await，毫秒）——/proc/diskstats 差分。
func diskAwait() (float64, bool) {
	var ticks, ops float64
	for _, line := range strings.Split(readFile("diskstats"), "\n") {
		f := strings.Fields(line)
		if len(f) < 13 {
			continue
		}
		name := f[2]
		// 只统计物理盘：sd/vd/xvd/nvme（跳过 loop/ram/dm 等虚拟设备）
		if !(strings.HasPrefix(name, "sd") || strings.HasPrefix(name, "vd") ||
			strings.HasPrefix(name, "xvd") || strings.HasPrefix(name, "nvme")) {
			continue
		}
		rT, _ := strconv.ParseFloat(f[6], 64)  // read ticks ms
		wT, _ := strconv.ParseFloat(f[10], 64) // write ticks ms
		rI, _ := strconv.ParseFloat(f[3], 64)  // reads completed
		wI, _ := strconv.ParseFloat(f[7], 64)  // writes completed
		ticks += rT + wT
		ops += rI + wI
	}
	if ops <= 0 {
		return 0, false
	}
	mu.Lock()
	oldTicks, oldOps, oldHas := prev.ioTicks, prev.ioOps, prev.hasIO
	prev.ioTicks, prev.ioOps, prev.hasIO = ticks, ops, true
	mu.Unlock()
	if !oldHas {
		return 0, false
	}
	dTicks, dOps := ticks-oldTicks, ops-oldOps
	if dOps <= 0 || dTicks < 0 {
		return 0, false // 空闲期无 IO 不上报
	}
	return math2(dTicks/dOps, 2), true
}

// tcpStates：/proc/net/tcp(6) 状态统计 + 连接表使用率（conntrack 优先，回退 file-nr）。
func tcpStates() (tw, total, connPct float64, ok bool) {
	for _, f := range []string{"net/tcp", "net/tcp6"} {
		for _, line := range strings.Split(readFile(f), "\n")[1:] {
			fields := strings.Fields(line)
			if len(fields) < 4 {
				continue
			}
			total++
			if fields[3] == "06" { // TCP_TIME_WAIT
				tw++
			}
		}
	}
	if total <= 0 {
		return 0, 0, 0, false
	}
	// 连接跟踪表使用率
	if data := readFile("sys/net/netfilter/nf_conntrack_count"); data != "" {
		cnt, err1 := strconv.ParseFloat(strings.TrimSpace(data), 64)
		maxData := readFile("sys/net/netfilter/nf_conntrack_max")
		mx, err2 := strconv.ParseFloat(strings.TrimSpace(maxData), 64)
		if err1 == nil && err2 == nil && mx > 0 {
			return tw, total, math2(cnt/mx*100, 2), true
		}
	}
	// 无 conntrack：回退打开文件数占比
	if data := readFile("sys/fs/file-nr"); data != "" {
		f := strings.Fields(data)
		if len(f) >= 3 {
			used, _ := strconv.ParseFloat(f[0], 64)
			mx, _ := strconv.ParseFloat(f[2], 64)
			if mx > 0 {
				return tw, total, math2(used/mx*100, 2), true
			}
		}
	}
	return tw, total, 0, true
}

// oomWatcher：/dev/kmsg 增量跟踪 OOM kill 事件（root 可读；无权限则禁用）。
type oomWatcher struct {
	mu     sync.Mutex
	f      *os.File
	offset int64
}

func newOomWatcher() *oomWatcher {
	f, err := os.Open("/dev/kmsg")
	if err != nil {
		return nil
	}
	// 从当前末尾开始（避免 agent 重启重放历史日志）
	end, err := f.Seek(0, io.SeekEnd)
	if err != nil {
		f.Close()
		return nil
	}
	return &oomWatcher{f: f, offset: end}
}

// kmsg OOM 记录解析：一次真实 OOM 通常是两条独立记录——
// "Out of memory: Kill process <pid> (<proc>) score <n>" 与续记录
// "Killed process <pid> (<proc>) ... anon-rss:<n>kB"（老内核合并为一条）。
var (
	reOOMKill   = regexp.MustCompile(`Kill process (\d+) \(([^)]*)\)(?: score (\d+))?`)
	reOOMKilled = regexp.MustCompile(`Killed process (\d+) \(([^)]*)\)`)
	reOOMRss    = regexp.MustCompile(`anon-rss:(\d+)kB`)
)

// collectOOM：返回自上次调用以来的 OOM kill 事件数，以及受害进程明细
// （"HH:MM:SS pid=N proc=NAME rss=NMB score=N"，供平台告警展示事故现场）。
func (w *oomWatcher) collectOOM() (int, []string) {
	if w == nil {
		return 0, nil
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	count := 0
	buf := make([]byte, 0, 64*1024)
	tmp := make([]byte, 4096)
	for {
		n, err := w.f.ReadAt(tmp, w.offset)
		if n > 0 {
			buf = append(buf, tmp[:n]...)
			w.offset += int64(n)
		}
		if err != nil {
			// kmsg 缓冲被清空（dmesg -C）或溢出覆盖后，旧 offset 失效返回 EPIPE，
			// 若不处理会永久失明：重开文件并自愈
			if errors.Is(err, syscall.EPIPE) {
				w.reopen()
			}
			break
		}
		if n == 0 {
			break
		}
	}
	type oomEvt struct {
		pid, proc, score, rss string
	}
	var events []*oomEvt
	pending := map[string]*oomEvt{} // pid → 等待 "Killed" 行补充内存占用的事件
	for _, line := range strings.Split(string(buf), "\n") {
		if m := reOOMKill.FindStringSubmatch(line); m != nil && strings.Contains(line, "Out of memory") {
			count++
			ev := &oomEvt{pid: m[1], proc: m[2], score: m[3]}
			events = append(events, ev)
			pending[m[1]] = ev
			continue
		}
		if m := reOOMKilled.FindStringSubmatch(line); m != nil {
			ev := pending[m[1]]
			if ev == nil {
				// 只捕到 "Killed" 行（"Kill" 行被缓冲覆盖）：同样计为一次 OOM
				count++
				ev = &oomEvt{pid: m[1], proc: m[2]}
				events = append(events, ev)
			}
			if rm := reOOMRss.FindStringSubmatch(line); rm != nil {
				ev.rss = rm[1]
			}
			delete(pending, m[1])
		}
	}
	details := make([]string, 0, len(events))
	stamp := time.Now().Format("15:04:05")
	for _, ev := range events {
		d := fmt.Sprintf("%s pid=%s proc=%s", stamp, ev.pid, ev.proc)
		if ev.rss != "" {
			if kb, err := strconv.Atoi(ev.rss); err == nil {
				d += fmt.Sprintf(" rss=%dMB", kb/1024)
			}
		}
		if ev.score != "" {
			d += " score=" + ev.score
		}
		details = append(details, d)
	}
	return count, details
}

// reopen：kmsg offset 失效（缓冲清空/溢出）后重开文件并从头读缓冲。
// 真实 OOM 时内核瞬间倾倒大量日志极易触发溢出，若跳到末尾会恰好丢掉刚发生的
// OOM 消息（漏报）；从头读最多重放少量旧消息导致重复计数，漏报比重复更严重。
func (w *oomWatcher) reopen() bool {
	f, err := os.Open("/dev/kmsg")
	if err != nil {
		return false
	}
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		f.Close()
		return false
	}
	w.f.Close()
	w.f = f
	w.offset = 0
	return true
}

// processCheck：/proc/<pid>/comm 扫描，返回缺失的关键进程列表。
func processCheck(items []string) []string {
	if len(items) == 0 {
		return nil
	}
	procs := map[string]bool{}
	entries, err := os.ReadDir(procDir)
	if err != nil {
		return nil
	}
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		if _, err := strconv.Atoi(e.Name()); err != nil {
			continue
		}
		comm := strings.TrimSpace(readFile(filepath.Join(e.Name(), "comm")))
		if comm != "" {
			procs[comm] = true
			// java 进程 comm 可能被截断，补充 cmdline 首段
			if comm == "java" {
				cmd := strings.Fields(readFile(filepath.Join(e.Name(), "cmdline")))
				if len(cmd) > 0 {
					procs[filepath.Base(strings.TrimRight(cmd[0], "\x00"))] = true
				}
			}
		}
	}
	var missing []string
	for _, item := range items {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		found := false
		for name := range procs {
			if strings.Contains(name, item) {
				found = true
				break
			}
		}
		if !found {
			missing = append(missing, item)
		}
	}
	return missing
}

// portCheck：本机端口 TCP 探活，返回探活失败端口。
func portCheck(ports []int) []string {
	var down []string
	for _, p := range ports {
		if p <= 0 || p > 65535 {
			continue
		}
		conn, err := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(p)), 2*time.Second)
		if err != nil {
			down = append(down, strconv.Itoa(p))
			continue
		}
		conn.Close()
	}
	return down
}

// netProbe：TCP 连接探测目标（丢包率% / 平均延迟 ms）。
func netProbe(target string, attempts int) (lossPct, latencyMs float64, ok bool) {
	if target == "" {
		target = "223.5.5.5:443"
	}
	if attempts <= 0 {
		attempts = 3
	}
	okN, totalMs := 0, 0.0
	for i := 0; i < attempts; i++ {
		start := time.Now()
		conn, err := net.DialTimeout("tcp", target, 2*time.Second)
		elapsed := time.Since(start).Seconds() * 1000
		if err == nil {
			conn.Close()
			okN++
			totalMs += elapsed
		}
	}
	latency := 0.0
	if okN > 0 {
		latency = math2(totalMs/float64(okN), 2)
	}
	return math2(float64(attempts-okN)/float64(attempts)*100, 2), latency, true
}

// bandwidthPct：出口/入口带宽使用率（/sys/class/net/<iface>/speed，Mbps）。
func bandwidthPct(rx, tx float64) (rxPct, txPct float64, ok bool) {
	iface := primaryIface()
	data, err := os.ReadFile(filepath.Join("/sys/class/net", iface, "speed"))
	if err != nil {
		return 0, 0, false
	}
	mbps, err := strconv.ParseFloat(strings.TrimSpace(string(data)), 64)
	if err != nil || mbps <= 0 {
		return 0, 0, false // 虚拟接口 speed 读取失败（-1）
	}
	capBps := mbps * 1e6 / 8
	if capBps <= 0 {
		return 0, 0, false
	}
	return math2(rx/capBps*100, 2), math2(tx/capBps*100, 2), true
}

// metricsScrape：抓取 Prometheus 文本指标 + URL 健康探测（连续失败计数）。
type scrapeState struct {
	mu      sync.Mutex
	streaks map[string]int // url -> 连续失败次数
	client  *http.Client
}

func newScrapeState() *scrapeState {
	return &scrapeState{
		streaks: map[string]int{},
		client:  &http.Client{Timeout: 4 * time.Second},
	}
}

func parsePromText(text string) map[string]float64 {
	out := map[string]float64{}
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		name := line
		if idx := strings.IndexAny(line, " {"); idx > 0 {
			name = line[:idx]
		}
		parts := strings.Fields(line)
		if len(parts) == 0 {
			continue
		}
		val, err := strconv.ParseFloat(parts[len(parts)-1], 64)
		if err != nil {
			continue
		}
		out[name] = val
	}
	return out
}

// collectMetrics：抓取全部 URL，返回 (指标聚合, 最大连续失败次数)。
func (s *scrapeState) collectMetrics(urls []string) (map[string]float64, int) {
	out := map[string]float64{}
	maxStreak := 0
	if s == nil {
		return out, 0
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, u := range urls {
		resp, err := s.client.Get(u)
		if err == nil {
			body, readErr := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
			resp.Body.Close()
			if readErr == nil && resp.StatusCode < 300 {
				for k, v := range parsePromText(string(body)) {
					out[k] = v
				}
				s.streaks[u] = 0
				continue
			}
		}
		s.streaks[u]++
		if s.streaks[u] > maxStreak {
			maxStreak = s.streaks[u]
		}
	}
	if len(out) > 0 {
		out["app_health_fail_streak"] = float64(maxStreak)
	}
	return out, maxStreak
}

// ---------- 采集编排 ----------

// effectiveItems：合并 collect_items 配置与告警策略开关（策略开启即采集对应项）。
func effectiveItems(items []string, p AlertPolicy) []string {
	set := map[string]bool{}
	for _, it := range items {
		set[it] = true
	}
	set["procs"] = true // 进程归因：所有告警场景通用（payload 附带 top 进程）
	if p.SwapEnabled {
		set["swap"] = true
	}
	if p.InodeEnabled {
		set["inode"] = true
	}
	if p.DiskIOEnabled {
		set["disk_io"] = true
	}
	if p.TcpConnEnabled {
		set["tcp"] = true
	}
	if p.OomEnabled {
		set["oom"] = true
	}
	if p.ProcessEnabled {
		set["process"] = true
	}
	if p.PortEnabled {
		set["port"] = true
	}
	if p.NetPerfEnabled {
		set["net_probe"] = true
	}
	if p.BandwidthEnabled {
		set["bandwidth"] = true
	}
	if p.MetricsEnabled {
		set["metrics"] = true
	}
	out := make([]string, 0, len(set))
	for it := range set {
		out = append(out, it)
	}
	return out
}

func (a *Agent) collect(items []string) map[string]interface{} {
	s := map[string]interface{}{"ts": float64(time.Now().UnixNano()) / 1e9}
	p := a.policy
	for _, it := range items {
		switch it {
		case "cpu":
			if v, ok := cpuPct(); ok {
				s["cpu"] = v
			}
		case "mem":
			if v, ok := memPct(); ok {
				s["mem"] = v
			}
			if total, used, ok := memKB(); ok {
				s["mem_total_mb"] = round2(total / 1024)
				s["mem_used_mb"] = round2(used / 1024)
			}
		case "disk":
			if v, ok := diskPct(); ok {
				s["disk"] = v
			}
		case "load":
			if l1, l5, l15, ncpu, ok := loadAvg(); ok {
				s["load1"], s["load5"], s["load15"] = l1, l5, l15
				if ncpu > 0 {
					s["ncpu"] = ncpu
				}
			}
		case "net":
			if rx, tx, ok := netRates(); ok {
				s["net_rx_bps"], s["net_tx_bps"] = rx, tx
			}
		case "procs":
			if tp := topProcs(); len(tp) > 0 {
				s["procs"] = tp
			}
		case "swap":
			if v, ok := swapPct(); ok {
				s["swap"] = v
			}
		case "inode":
			if v, ok := inodePct(); ok {
				s["inode"] = v
			}
		case "disk_io":
			if v, ok := diskAwait(); ok {
				s["await_ms"] = v
			}
		case "tcp":
			if tw, total, pct, ok := tcpStates(); ok {
				s["tcp_tw"], s["tcp_total"] = tw, total
				if pct > 0 {
					s["tcp_conn_pct"] = pct
				}
			}
		case "oom":
			n, det := a.oom.collectOOM()
			if n > 0 {
				s["oom_events"] = float64(n)
			}
			if len(det) > 0 {
				s["oom_detail"] = det
			}
		case "process":
			if missing := processCheck(p.ProcessItems); len(missing) > 0 {
				s["procs_missing"] = missing
			}
		case "port":
			if down := portCheck(p.PortItems); len(down) > 0 {
				s["ports_down"] = down
			}
		case "net_probe":
			if loss, lat, ok := netProbe(p.NetProbeTarget, 3); ok {
				s["loss_pct"], s["latency_ms"] = loss, lat
			}
		case "bandwidth":
			rx, tx := a.cachedNetRates()
			if rxPct, txPct, ok := bandwidthPct(rx, tx); ok {
				s["bw_rx_pct"], s["bw_tx_pct"] = rxPct, txPct
			}
		case "metrics":
			if m, _ := a.scrape.collectMetrics(p.MetricsUrls); len(m) > 0 {
				s["metrics"] = m
			}
		}
	}
	return s
}

// cachedNetRates：取最近一次 net 采集的速率（bandwidth 依赖 net 速率）。
func (a *Agent) cachedNetRates() (float64, float64) {
	a.netMu.Lock()
	defer a.netMu.Unlock()
	return a.lastRx, a.lastTx
}

// ---------- 服务器详情（低频本地采集，替代 SSH 上机） ----------

// sysinfoCmd：本地一次采集服务器全景（与后端 inspector.parse_sysinfo_raw 配套解析）。
const sysinfoCmd = `echo @@BASIC@@; hostname 2>/dev/null; uname -r; uname -m; sed -n 's/^PRETTY_NAME="\{0,1\}\(.*\)"\{0,1\}$/\1/p' /etc/os-release 2>/dev/null; date '+%Y-%m-%d %H:%M:%S %z'; readlink /etc/localtime 2>/dev/null | sed 's|.*zoneinfo/||'; uptime -s 2>/dev/null;
echo @@VIRT@@; systemd-detect-virt 2>/dev/null; grep -c hypervisor /proc/cpuinfo 2>/dev/null; cat /sys/class/dmi/id/product_name 2>/dev/null; cat /sys/class/dmi/id/sys_vendor 2>/dev/null;
echo @@CPU@@; lscpu 2>/dev/null; grep -m1 'model name' /proc/cpuinfo 2>/dev/null; nproc 2>/dev/null;
echo @@MEMINFO@@; grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree|SwapCached|Dirty|Writeback|Active|Inactive|Slab):' /proc/meminfo 2>/dev/null; free -m 2>/dev/null | sed -n '2,3p';
echo @@DISKS@@; df -hP 2>/dev/null;
echo @@INODES@@; df -iP 2>/dev/null;
echo @@BLOCKS@@; lsblk -P -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL 2>/dev/null;
echo @@IFACES@@; ip -brief address 2>/dev/null || ip -o -4 addr show 2>/dev/null;
echo @@ROUTE@@; ip route 2>/dev/null;
echo @@TCP@@; ss -s 2>/dev/null | head -n 6;
echo @@LISTEN@@; ss -tulnpH 2>/dev/null | head -n 40;
echo @@PROCS@@; ps -eo pid --no-headers 2>/dev/null | wc -l; ps -eo stat --no-headers 2>/dev/null | grep -c '^R'; ps -eo stat --no-headers 2>/dev/null | grep -c '^Z';
echo @@USERS@@; who 2>/dev/null;
echo @@AGENT@@; ps -eo args 2>/dev/null | grep -E 'devops-agent' | grep -v grep | head -n 3; /opt/devops-agent/agent --version 2>/dev/null | head -n 1;
echo @@END@@`

// collectSysinfo：本地 shell 一次采集服务器全景原始文本（失败返回空串）。
func collectSysinfo() string {
	out, err := exec.Command("/bin/sh", "-c", sysinfoCmd).Output()
	if err != nil {
		return ""
	}
	return string(out)
}

// ---------- 上报 ----------

type Agent struct {
	server, token, assetID string
	cfg                    AgentCfg
	policy                 AlertPolicy
	// buf 平铺存储待补发样本，与平台 samples: list[dict] 结构对齐（勿改回二维批次）
	buf            []map[string]interface{}
	bufLock        sync.Mutex
	client         *http.Client
	oom            *oomWatcher
	scrape         *scrapeState
	netMu          sync.Mutex
	lastRx         float64
	lastTx         float64
	sysinfoMu      sync.Mutex
	pendingSysinfo string
}

func (a *Agent) do(method, path string, body []byte) ([]byte, error) {
	req, err := http.NewRequest(method, a.server+path, strings.NewReader(string(body)))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Agent-Token", a.token)
	resp, err := a.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 300 {
		return nil, fmt.Errorf("http %d: %s", resp.StatusCode, string(data))
	}
	return data, nil
}

func (a *Agent) syncConfig() {
	data, err := a.do("GET", "/api/v1/agent/config?asset_id="+a.assetID, nil)
	if err != nil {
		log.Printf("[agent] sync config failed: %v", err)
		return
	}
	var out struct {
		Config      AgentCfg    `json:"config"`
		AlertPolicy AlertPolicy `json:"alert_policy"`
	}
	if json.Unmarshal(data, &out) == nil && out.Config.ReportInterval > 0 {
		a.cfg = out.Config
		a.policy = out.AlertPolicy
	}
}

func (a *Agent) flush(ready []map[string]interface{}) {
	a.bufLock.Lock()
	samples := append([]map[string]interface{}{}, a.buf...)
	a.buf = a.buf[:0]
	a.bufLock.Unlock()
	if len(ready) > 0 {
		samples = append(samples, ready...)
	}
	if len(samples) == 0 {
		return
	}
	a.sysinfoMu.Lock()
	sys := a.pendingSysinfo
	a.sysinfoMu.Unlock()
	payloadMap := map[string]interface{}{
		"asset_id": a.assetID, "agent_version": agentVersion, "samples": samples,
	}
	if sys != "" {
		payloadMap["sysinfo"] = sys
	}
	payload, err := json.Marshal(payloadMap)
	if err != nil {
		log.Printf("[agent] marshal report failed: %v (samples=%d dropped)", err, len(samples))
		return
	}
	if _, err := a.do("POST", "/api/v1/agent/report", payload); err != nil {
		log.Printf("[agent] report failed: %v (samples=%d buffered)", err, len(samples))
		a.bufLock.Lock()
		a.buf = append(samples, a.buf...)
		if len(a.buf) > a.cfg.BufferMax {
			a.buf = a.buf[len(a.buf)-a.cfg.BufferMax:]
		}
		a.bufLock.Unlock()
	} else if sys != "" {
		// 仅成功上报后清空，失败保留待下次补发
		a.sysinfoMu.Lock()
		if a.pendingSysinfo == sys {
			a.pendingSysinfo = ""
		}
		a.sysinfoMu.Unlock()
	}
}

func (a *Agent) run(stop <-chan struct{}) {
	a.syncConfig()
	nextCfg := time.Now().Add(time.Duration(a.cfg.ConfigRefresh) * time.Second)
	nextSysinfo := time.Now().Add(10 * time.Second)
	ready := make([]map[string]interface{}, 0, 8)
	nextCollect, nextReport := time.Time{}, time.Time{}
	log.Printf("[agent] v%s start -> %s asset=%s", agentVersion, a.server, a.assetID)
	for {
		select {
		case <-stop:
			return
		default:
		}
		now := time.Now()
		if !now.Before(nextCollect) {
			items := effectiveItems(a.cfg.CollectItems, a.policy)
			frame := a.collect(items)
			// 缓存网络速率供 bandwidth 使用
			if rx, ok := frame["net_rx_bps"].(float64); ok {
				a.netMu.Lock()
				a.lastRx = rx
				if tx, ok2 := frame["net_tx_bps"].(float64); ok2 {
					a.lastTx = tx
				}
				a.netMu.Unlock()
			}
			ready = append(ready, frame)
			nextCollect = now.Add(time.Duration(a.cfg.CollectInterval) * time.Second)
		}
		if !now.Before(nextReport) {
			a.flush(ready)
			ready = ready[:0]
			nextReport = now.Add(time.Duration(a.cfg.ReportInterval) * time.Second)
		}
		if !now.Before(nextCfg) {
			a.syncConfig()
			nextCfg = now.Add(time.Duration(a.cfg.ConfigRefresh) * time.Second)
		}
		if !now.Before(nextSysinfo) {
			if s := collectSysinfo(); s != "" {
				a.sysinfoMu.Lock()
				a.pendingSysinfo = s
				a.sysinfoMu.Unlock()
			}
			nextSysinfo = now.Add(120 * time.Second)
		}
		time.Sleep(200 * time.Millisecond)
	}
}

func main() {
	cfgPath := flag.String("config", "config.json", "配置文件路径")
	server := flag.String("server", "", "平台地址")
	token := flag.String("token", "", "上报令牌")
	assetID := flag.String("asset-id", "", "资产 ID")
	flag.Parse()

	cfg := loadConfig(*cfgPath)
	if *server != "" {
		cfg.Server = *server
	}
	if *token != "" {
		cfg.Token = *token
	}
	if *assetID != "" {
		cfg.AssetID = *assetID
	}
	if cfg.Server == "" || cfg.Token == "" || cfg.AssetID == "" {
		log.Fatal("[agent] 缺少 server/token/asset_id 配置")
	}
	a := &Agent{
		server: strings.TrimRight(cfg.Server, "/"), token: cfg.Token, assetID: cfg.AssetID,
		cfg:    cfg.AgentConfig,
		client: &http.Client{Timeout: 5 * time.Second},
		oom:    newOomWatcher(),
		scrape: newScrapeState(),
	}
	stop := make(chan struct{})
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	go func() { <-sig; close(stop) }()
	a.run(stop)
}
