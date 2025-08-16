
# =============================================
# topology_2subnets_6hosts.py
# ---------------------------------------------
# Mininet topology: **2 subnets**, **5 switches per subnet** (total 10 OVS),
# **6 hosts total** (3 hosts per subnet). Inter-subnet routing via LinuxRouter r0.
# Automatically runs iperf TCP & UDP **between all hosts** after deployment.
# ---------------------------------------------
# Run controller (in another terminal):
#   python -m ryu.cmd.manager --ofp-tcp-listen-port 6653 \
#     ryu_app_learning_switch.py ryu_app_firewall.py ryu_app_load_balancer.py ryu_app_telemetry.py
# Run this topology:
#   sudo python3 topology_2subnets_6hosts.py
# =============================================

from mininet.topo import Topo
from mininet.node import Node, RemoteController, OVSKernelSwitch
from mininet.net import Mininet
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info
import time

class LinuxRouter(Node):
    """A Node with IP forwarding enabled so it acts as a router."""
    def config(self, **params):
        super(LinuxRouter, self).config(**params)
        self.cmd('sysctl -w net.ipv4.ip_forward=1')
    def terminate(self):
        self.cmd('sysctl -w net.ipv4.ip_forward=0')
        super(LinuxRouter, self).terminate()

class TwoSubnetSmallTopo(Topo):
    """
    Subnet1: 10.0.1.0/24, gateway 10.0.1.254 (r0-eth1)
    Subnet2: 10.0.2.0/24, gateway 10.0.2.254 (r0-eth2)
    5 switches per subnet (star), 3 hosts per subnet (6 total).
    """
    def build(self, **params):
        r0 = self.addNode('r0', cls=LinuxRouter, ip='10.0.1.254/24')

        def add_subnet(subnet_id: int, host_count: int):
            # 5 switches: sX0 main + sX1..sX4 leaves
            s_main = self.addSwitch(f's{subnet_id}0', protocols='OpenFlow13')
            leaves = [self.addSwitch(f's{subnet_id}{i}', protocols='OpenFlow13') for i in range(1,5)]
            for s in leaves:
                self.addLink(s_main, s)
            # Router uplink
            r_if = f'r0-eth{subnet_id}'
            r_ip = f'10.0.{subnet_id}.254/24'
            self.addLink(r0, s_main, intfName1=r_if, params1={'ip': r_ip})
            # Hosts: spread across the 5 switches (some will have 0/1 host)
            cycle = [s_main] + leaves
            for i in range(1, host_count + 1):
                hip = f'10.0.{subnet_id}.{i}/24'
                h = self.addHost(f'h{subnet_id}_{i:02d}', ip=hip, defaultRoute=f'via 10.0.{subnet_id}.254')
                self.addLink(h, cycle[(i-1) % 5])

        add_subnet(1, host_count=3)
        add_subnet(2, host_count=3)

# ---- iperf helpers: run tests across ALL hosts ----

def _which(host, cmd):
    path = host.cmd(f"which {cmd} 2>/dev/null").strip()
    return path if path else None


def _all_hosts(net):
    names = ['h1_01','h1_02','h1_03','h2_01','h2_02','h2_03']
    return [net.get(n) for n in names if n in net]


def run_iperf_all_hosts(net):
    info('
*** Running iperf across all hosts (TCP & UDP)
')
    hosts = _all_hosts(net)
    if not hosts:
        info('No hosts found for iperf tests.
')
        return

    # Prefer iperf3 if present on first host; otherwise iperf
    use_iperf3 = bool(_which(hosts[0], 'iperf3'))
    server_cmd = 'iperf3 -s -D' if use_iperf3 else 'iperf -s -D'
    pkill_cmd = 'pkill -f iperf3' if use_iperf3 else 'pkill -f iperf'

    if use_iperf3:
        tcp_client = lambda h, dst: h.cmd(f'iperf3 -c {dst} -t 3 -i 1')
        udp_client = lambda h, dst: h.cmd(f'iperf3 -u -b 20M -c {dst} -t 3 -i 1')
        port = 5201
    else:
        tcp_client = lambda h, dst: h.cmd(f'iperf -c {dst} -t 3 -i 1')
        udp_client = lambda h, dst: h.cmd(f'iperf -u -b 20M -c {dst} -t 3 -i 1')
        port = 5001

    # Start servers on all hosts
    for h in hosts:
        h.cmd(server_cmd)
    time.sleep(1)
    info(f"* iperf servers started on {len(hosts)} hosts (port {port})
")

    # Run tests pairwise (sequential to avoid interference)
    for src in hosts:
        for dst in hosts:
            if src is dst:
                continue
            info(f"
* TCP: {src.name} -> {dst.IP()}
")
            info(tcp_client(src, dst.IP()))
            info(f"* UDP: {src.name} -> {dst.IP()} (20M)
")
            info(udp_client(src, dst.IP()))

    # Cleanup servers
    for h in hosts:
        h.cmd(pkill_cmd)
    info('
*** iperf tests completed and servers stopped.
')


def run():
    setLogLevel('info')
    topo = TwoSubnetSmallTopo()
    net = Mininet(
        topo=topo,
        controller=None,
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True,
        autoStaticArp=False,
    )
    info('
*** Adding remote controller (Ryu @ 127.0.0.1:6653)
')
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)

    info('
*** Starting network
')
    net.start()

    r0 = net.get('r0')
    info('
*** Router interfaces:
')
    info(r0.cmd('ip -br -c a'))

    # Quick gateway pings
    for sid in (1,2):
        h = net.get(f'h{sid}_01')
        info(f'
Pinging gateway from {h.name}:
')
        info(h.cmd(f'ping -c2 10.0.{sid}.254'))

    # Run iperf across all hosts
    run_iperf_all_hosts(net)

    info('
*** Mininet CLI (type exit to stop)
')
    CLI(net)
    net.stop()

if __name__ == '__main__':
    run()
