# =============================================
# topology_multisubnet.py
# ---------------------------------------------
# Mininet topology: 3 subnets, each with 5 switches and 25 hosts.
# Inter-subnet routing via a LinuxRouter node (r0).
# Switches use OpenFlow 1.3 and connect to a remote Ryu controller.
# ---------------------------------------------
# Run controller (in another terminal):
#   ryu-manager ryu_app_learning_switch.py ryu_app_firewall.py ryu_app_load_balancer.py
# Run this topology:
#   sudo python3 topology_multisubnet.py
# or:
#   sudo mn --custom topology_multisubnet.py --topo ms3 --controller=remote,ip=127.0.0.1,port=6653 --switch ovs,protocols=OpenFlow13
# =============================================

from mininet.topo import Topo
from mininet.node import Node, RemoteController, OVSKernelSwitch
from mininet.net import Mininet
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

class LinuxRouter(Node):
    """A Node with IP forwarding enabled so it acts as a router."""
    def config(self, **params):
        super(LinuxRouter, self).config(**params)
        self.cmd('sysctl -w net.ipv4.ip_forward=1')
        # Optional: reduce ARP flux issues
        # (removed ARP sysctls to avoid over-restrictive replies)
        # self.cmd('sysctl -w net.ipv4.conf.all.arp_ignore=1')
        # self.cmd('sysctl -w net.ipv4.conf.all.arp_announce=2')

    def terminate(self):
        self.cmd('sysctl -w net.ipv4.ip_forward=0')
        super(LinuxRouter, self).terminate()

class MultiSubnetTopo(Topo):
    """
    3 subnets (10.0.1.0/24, 10.0.2.0/24, 10.0.3.0/24)
    Each subnet has 5 switches: sX0 (main), sX1..sX4 (leafs)
    Each of the 5 switches carries 5 hosts => 25 hosts per subnet
    Router r0 has 3 interfaces: r0-eth1(10.0.1.254/24), r0-eth2(10.0.2.254/24), r0-eth3(10.0.3.254/24)
    """

    def build(self, **params):
        # Create router
        r0 = self.addNode('r0', cls=LinuxRouter, ip='10.0.1.254/24')

        # Helper to add a subnet bundle
        def add_subnet(subnet_id: int):
            # Switch names
            s_main = self.addSwitch(f's{subnet_id}0', protocols='OpenFlow13')
            s_leaf = [self.addSwitch(f's{subnet_id}{i}', protocols='OpenFlow13') for i in range(1,5)]

            # Connect main switch to leaves (star)
            for s in s_leaf:
                self.addLink(s_main, s)

            # Connect router to main switch
            r_if = f'r0-eth{subnet_id}'
            r_ip = f'10.0.{subnet_id}.254/24'
            self.addLink(r0, s_main, intfName1=r_if, params1={'ip': r_ip})

            # Add 25 hosts, 5 per switch (main + 4 leafs)
            switches_cycle = [s_main] + s_leaf  # length 5
            for i in range(1, 26):
                host_ip = f'10.0.{subnet_id}.{i}/24'
                h = self.addHost(f'h{subnet_id}_{i:02d}', ip=host_ip, defaultRoute=f'via 10.0.{subnet_id}.254')
                target_sw = switches_cycle[(i-1) % 5]
                # TCLink could be used to shape bandwidth/latency if desired
                self.addLink(h, target_sw)

        add_subnet(1)
        add_subnet(2)
        add_subnet(3)

# Convenience launcher

def run():
    setLogLevel('info')
    topo = MultiSubnetTopo()
    net = Mininet(
        topo=topo,
        controller=None,  # we'll add a RemoteController
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=True,  # deterministic MACs are handy
        autoStaticArp=False
    )

    info('\n*** Adding remote controller (Ryu @ 127.0.0.1:6653)\n')
    c0 = net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6653)

    info('\n*** Starting network\n')
    net.start()

    # Optional: print some info
    info('\n*** Router interfaces:\n')
    r0 = net.get('r0')
    info(r0.cmd('ip -br -c a'))

    info('\n*** Test: ping gateway in each subnet from first host\n')
    for sid in (1,2,3):
        h = net.get(f'h{sid}_01')
        info(f"\nPinging gateway from {h.name}:\n")
        info(h.cmd(f'ping -c2 10.0.{sid}.254'))

    info('\n*** Mininet CLI: try pingall, iperf, etc.\n')
    CLI(net)

    info('\n*** Stopping network\n')
    net.stop()

if __name__ == '__main__':
    run()


