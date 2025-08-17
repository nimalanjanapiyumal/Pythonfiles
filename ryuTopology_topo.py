
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel, info

def treeNet():
    "Create an empty network and add nodes to it."

    net = Mininet(topo = None,controller=RemoteController )

    info( '*** Adding controller\n' )
    c0 = net.addController(name='c0',link=TCLink)

    info( '*** Adding hosts\n' )
    h1 = net.addHost( 'h1', ip='10.0.0.1/24', mac='00:00:00:00:00:01' )
    h2 = net.addHost( 'h2', ip='10.0.0.2/24', mac='00:00:00:00:00:02' )
    h3 = net.addHost( 'h3', ip='10.0.0.3/24', mac='00:00:00:00:00:03' )
    h4 = net.addHost( 'h4', ip='10.0.0.4/24', mac='00:00:00:00:00:04' )
    h5 = net.addHost( 'h5', ip='10.0.0.5/24', mac='00:00:00:00:00:05' )
    h6 = net.addHost( 'h6', ip='10.0.0.6/24', mac='00:00:00:00:00:06' )
    
                                                                     

    info( '*** Adding switch\n' )
    s1 = net.addSwitch( 's1' )    
    s2 = net.addSwitch( 's2' ) 
    s3 = net.addSwitch( 's3' )    
    s4 = net.addSwitch( 's4' )    
    s5 = net.addSwitch( 's5' )    
    s6 = net.addSwitch( 's6' )    
    

    info( '*** Creating host links\n' )
    net.addLink( h1, s1 )
    net.addLink( h2, s2 )
    net.addLink( h3, s3 )
    net.addLink( h4, s4 )
    net.addLink( h5, s5 )
    net.addLink( h6, s6 )

    info( '*** Creating switch links\n' )
    net.addLink( s1, s2,cls= TCLink,bw=10)
    net.addLink( s1, s3,cls= TCLink,bw=10)
    net.addLink( s1, s5,cls= TCLink,bw=5)
    net.addLink( s5, s6,cls= TCLink,bw=5)
    net.addLink( s3, s4,cls= TCLink,bw=5)

    info( '*** Starting network\n')
    net.start()

    # 1) Print configured TCLink bandwidths
    info('\n*** Printing configured TCLink bandwidths:\n')
    for link in net.links:
        intf1, intf2 = link.intf1, link.intf2

        # Pick one endpoint (intf1.node) to run 'tc qdisc show' on
        owner = intf1.node
        intf  = intf1.name
        tc_output = owner.cmd(f'tc qdisc show dev {intf}')

        info(f'  Link: {intf1.node.name}:{intf1.name} <--> {intf2.node.name}:{intf2.name}\n')
        info(f'    {owner.name} sees TC on {intf}:\n{tc_output.strip()}\n')

    # 2) Simple iperf bandwidth test between h1 <-> h2
    info('\n*** Running iperf test between h1 <-> h2\n')
    # Start iperf server on h2
    iperf_server = h2.popen('iperf -s -p 5001')
    time.sleep(1)  # give the server a moment to spin up

    # Run iperf client on h1 for 5 seconds
    client_output = h1.cmd('iperf -c 10.0.0.2 -p 5001 -t 5')
    info(f'*** h1 -> h2 iperf results:\n{client_output.strip()}\n')

    # Stop the server
    iperf_server.terminate()
    time.sleep(1)

    # 3) Launch nload on each host to visualize live traffic
    info('\n*** Launching nload on each host (in a new xterm window):\n')
    for host in [h1, h2, h3, h4, h5, h6]:
        intf = f'{host.name}-eth0'
        # This will open an xterm (if X is available) and run nload for that interface
        host.cmd(f'xterm -T "nload {host.name}" -e "nload -u M {intf}" &')
        info(f'  Started nload on {host.name} (interface: {intf})\n')

    info('\n*** Visualization setup complete. Entering Mininet CLI...\n')
    info('    Watch the nload windows for live TX/RX rates once you generate traffic.\n')

    CLI(net)

    # 4) After exiting CLI, clean up any nload processes on each host
    info('\n*** Cleaning up nload processes on all hosts\n')
    for host in [h1, h2, h3, h4, h5, h6]:
        host.cmd('pkill nload')





    info( '*** Running CLI\n' )
    CLI( net )

    info( '*** Stopping network' )
    net.stop()
if __name__ == '__main__':
    setLogLevel( 'info' )
    treeNet()
