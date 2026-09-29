// DevOpsAgent 子机采集探针（Go 实现，静态编译单二进制，零运行时依赖）。
//
// 功能与 agent.py 对齐：按配置间隔采集 /proc（CPU/内存/磁盘/负载/网络速率），
// 推送平台 /api/v1/agent/report；断网本地环形缓存补发；定期同步平台配置。
//
// 构建：make build-go   （产出 dist/agent-linux-amd64）
// 运行：./agent --config config.json
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const agentVersion = "1.0.0-go"
const procDir = "/proc"

var skipIfacePrefixes = []string{"lo", "docker", "veth", "br-", "virbr", "tun", "tap", "flannel", "cni", "cali"}

// ---------- 配置 ----------

type Config struct {
	Server         string   `json:"server"`
	Token          string   `json:"token"`
	AssetID        string   `json:"asset_id"`
	AgentConfig    AgentCfg `json:"agent_config"`
}

type AgentCfg struct {
	ReportInterval  int      `json:"report_interval"`
	CollectInterval int      `json:"collect_interval"`
	CollectItems    []string `json:"collect_items"`
	BufferMax       int      `json:"buffer_max"`
	ConfigRefresh   int      `json:"config_refresh"`
	OfflineAfter    int      `json:"offline_after"`
}

func defaults() AgentCfg {
	return AgentCfg{
		ReportInterval: 5, CollectInterval: 5,
		CollectItems:  []string{"cpu", "mem", "disk", "load", "net"},
		BufferMax:     600, ConfigRefresh: 300, OfflineAfter: 30,
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

// ---------- /proc 采集 ----------

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
}

var (
	mu       sync.Mutex
	prev     snapshot
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

func collect(items []string) map[string]interface{} {
	s := map[string]interface{}{"ts": float64(time.Now().UnixNano()) / 1e9}
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
		case "disk":
			if v, ok := diskPct(); ok {
				s["disk"] = v
			}
		case "load":
			if v, ok := load1(); ok {
				s["load1"] = v
			}
		case "net":
			if rx, tx, ok := netRates(); ok {
				s["net_rx_bps"], s["net_tx_bps"] = rx, tx
			}
		}
	}
	return s
}

// ---------- 上报 ----------

type Agent struct {
	server, token, assetID string
	cfg                    AgentCfg
	buf                    [][]map[string]interface{}
	bufLock                sync.Mutex
	client                 *http.Client
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
	buf := make([]byte, 4096)
	n, _ := resp.Body.Read(buf)
	if resp.StatusCode >= 300 {
		return nil, fmt.Errorf("http %d: %s", resp.StatusCode, string(buf[:n]))
	}
	return buf[:n], nil
}

func (a *Agent) syncConfig() {
	data, err := a.do("GET", "/api/v1/agent/config?asset_id="+a.assetID, nil)
	if err != nil {
		return
	}
	var out struct {
		Config AgentCfg `json:"config"`
	}
	if json.Unmarshal(data, &out) == nil && out.Config.ReportInterval > 0 {
		a.cfg = out.Config
	}
}

func (a *Agent) flush(ready []map[string]interface{}) {
	a.bufLock.Lock()
	samples := append([][]map[string]interface{}{}, a.buf...)
	a.buf = a.buf[:0]
	a.bufLock.Unlock()
	if ready != nil {
		samples = append(samples, ready)
	}
	if len(samples) == 0 {
		return
	}
	payload, _ := json.Marshal(map[string]interface{}{
		"asset_id": a.assetID, "agent_version": agentVersion, "samples": samples,
	})
	if _, err := a.do("POST", "/api/v1/agent/report", payload); err != nil {
		a.bufLock.Lock()
		a.buf = append(samples, a.buf...)
		if len(a.buf) > a.cfg.BufferMax {
			a.buf = a.buf[len(a.buf)-a.cfg.BufferMax:]
		}
		a.bufLock.Unlock()
	}
}

func (a *Agent) run(stop <-chan struct{}) {
	a.syncConfig()
	nextCfg := time.Now().Add(time.Duration(a.cfg.ConfigRefresh) * time.Second)
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
			ready = append(ready, collect(a.cfg.CollectItems))
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
		cfg: cfg.AgentConfig,
		client: &http.Client{Timeout: 5 * time.Second},
	}
	stop := make(chan struct{})
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	go func() { <-sig; close(stop) }()
	a.run(stop)
}
