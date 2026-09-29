// Zabbix 触发器英文名 → 中文（异常项展示用）
export const TRIGGER_CN = [
  ['High memory utilization', '内存利用率过高'],
  ['High CPU utilization', 'CPU 利用率过高'],
  ['Load average is too high', '系统负载过高'],
  ['Lack of free swap space', '交换空间不足'],
  ['High swap space usage', '交换空间使用率过高'],
  ['Unavailable by ICMP ping', '主机不可达（ICMP）'],
  ['Free disk space is less than', '磁盘剩余空间不足'],
  ['Processor load is too high', '处理器负载过高'],
  ['Too many processes running', '运行进程数过多'],
  ['Too many processes on', '进程数过多'],
  ['Zabbix agent is not available', 'Zabbix agent 不可用'],
  ['Zabbix agent on', 'Zabbix agent 不可达'],
]

export function cnTrigger(name) {
  if (!name) return name
  for (const [en, cn] of TRIGGER_CN) {
    if (name.startsWith(en)) return name.replace(en, cn)
  }
  return name
}
