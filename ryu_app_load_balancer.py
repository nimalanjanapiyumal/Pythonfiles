# =============================================
# ryu_app_load_balancer.py (Optional)
# ---------------------------------------------
# Minimal VIP load balancer with ARP proxy for the VIP.
# Distributes flows across a server pool (round-robin) and rewrites L3/L2 on ingress switch.
# NOTE: This demo assumes servers are in subnet 10.0.1.0/24 and VIP is 10.0.1.100.
# It installs NAT-like flows on the switch that first sees the packet (ingress).
# This is suitable for demonstrating controller-driven load balancing, not production.
# =============================================

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet
from ryu.lib.packet import ethernet, ether_types
from ryu.lib.packet import ipv4, arp

class SimpleLoadBalancer(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    VIP_IP = '10.0.1.100'
    VIP_MAC = '00:aa:bb:cc:dd:ee'  # virtual MAC for VIP ARP replies
    # Backend servers (populate dynamically if MAC unknown)
    SERVER_POOL = [
        {'ip': '10.0ping1.1', 'mac': None},
        {'ip': '10.0.1.2', 'mac': None},
    ]

    def __init__(self, *args, **kwargs):
        super(SimpleLoadBalancer, self).__init__(*args, **kwargs)
        self.rr_index = 0
        self.ip2mac = {}  # observed IP->MAC mapping

    def _choose_server(self):
        server = self.SERVER_POOL[self.rr_index]
        self.rr_index = (self.rr_index + 1) % len(self.SERVER_POOL)
        return server

    def _add_flow(self, dp, priority, match, actions, idle_timeout=30, hard_timeout=0):
        ofp = dp.ofproto; parser = dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=dp, priority=priority, match=match,
                                instructions=inst, idle_timeout=idle_timeout,
                                hard_timeout=hard_timeout)
        dp.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features(self, ev):
        # No table-miss here; LearningSwitch app will install it.
        self.logger.info("LB active on switch %s", ev.msg.datapath.id)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        parser = dp.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        # Learn IP->MAC from ARP/IP packets
        ip4 = pkt.get_protocol(ipv4.ipv4)
        if ip4 and eth.src:
            self.ip2mac[ip4.src] = eth.src

        # Handle ARP proxy for VIP
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt:
            # Cache sender mapping
            self.ip2mac[arp_pkt.src_ip] = arp_pkt.src_mac
            if arp_pkt.opcode == arp.ARP_REQUEST and arp_pkt.dst_ip == self.VIP_IP:
                # Craft ARP reply: VIP replies with VIP_MAC
                self.logger.info("LB: ARP request for VIP from %s (%s) on sw=%s", arp_pkt.src_ip, arp_pkt.src_mac, dp.id)
                e = ethernet.ethernet(dst=arp_pkt.src_mac, src=self.VIP_MAC, ethertype=ether_types.ETH_TYPE_ARP)
                a = arp.arp(opcode=arp.ARP_REPLY,
                            src_mac=self.VIP_MAC, src_ip=self.VIP_IP,
                            dst_mac=arp_pkt.src_mac, dst_ip=arp_pkt.src_ip)
                rep = packet.Packet()
                rep.add_protocol(e)
                rep.add_protocol(a)
                rep.serialize()
                actions = [parser.OFPActionOutput(in_port)]
                out = parser.OFPPacketOut(datapath=dp, buffer_id=ofp.OFP_NO_BUFFER,
                                          in_port=ofp.OFPP_CONTROLLER, actions=actions, data=rep.data)
                dp.send_msg(out)
                return
            # For other ARP, let LearningSwitch handle/flood

        # Intercept IPv4 traffic to VIP and NAT to a backend server
        if ip4 and ip4.dst == self.VIP_IP:
            server = self._choose_server()
            # Fill MAC if we know it; otherwise try to get from cache
            if not server['mac']:
                server['mac'] = self.ip2mac.get(server['ip'])
            if not server['mac']:
                # No MAC known yet; flood current packet and wait for ARP learning
                self.logger.info("LB: server MAC unknown for %s, flooding packet", server['ip'])
                actions = [parser.OFPActionOutput(ofp.OFPP_FLOOD)]
                out = parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                          in_port=in_port, actions=actions, data=msg.data)
                dp.send_msg(out)
                return

            client_mac = eth.src
            client_ip = ip4.src

            # Forward path: match dst VIP -> rewrite to server IP/MAC and output toward server
            # We don't know the exact output port to reach server on this switch; rely on normal L2 learning
            # by setting eth_dst to server MAC and output FLOOD for first packet. Then LearningSwitch will learn.
            match_fwd = parser.OFPMatch(eth_type=0x0800, ipv4_dst=self.VIP_IP)
            actions_fwd = [
                parser.OFPActionSetField(ipv4_dst=server['ip']),
                parser.OFPActionSetField(eth_dst=server['mac']),
                parser.OFPActionOutput(ofp.OFPP_NORMAL if hasattr(ofp, 'OFPP_NORMAL') else ofp.OFPP_FLOOD)
            ]
            self._add_flow(dp, priority=10, match=match_fwd, actions=actions_fwd)

            # Return path: packets from server -> rewrite to VIP and back to client MAC/port
            match_back = parser.OFPMatch(eth_type=0x0800, ipv4_src=server['ip'], ipv4_dst=client_ip)
            actions_back = [
                parser.OFPActionSetField(ipv4_src=self.VIP_IP),
                parser.OFPActionSetField(eth_src=self.VIP_MAC),
                parser.OFPActionOutput(in_port)
            ]
            self._add_flow(dp, priority=10, match=match_back, actions=actions_back)

            # Also forward the current packet (post-rewrite) using PacketOut for immediate effect
            actions_now = [
                parser.OFPActionSetField(ipv4_dst=server['ip']),
                parser.OFPActionSetField(eth_dst=server['mac']),
                parser.OFPActionOutput(ofp.OFPP_FLOOD)
            ]
            out = parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                      in_port=in_port, actions=actions_now, data=msg.data)
            dp.send_msg(out)
            self.logger.info("LB: %s -> VIP(%s) mapped to backend %s (%s) on sw=%s", client_ip, self.VIP_IP, server['ip'], server['mac'], dp.id)
            return
        # Otherwise, do nothing; LearningSwitch handles it.
