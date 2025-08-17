# =============================================
# ryu_app_firewall.py
# ---------------------------------------------
# Simple stateless firewall (default-allow). Installs high-priority drop rules
# for configured patterns (ICMP, SSH, host-to-host, etc.).
# Optional: provide JSON rules file via env FIREWALL_RULES=/path/to/rules.json
# Rule format examples:
#   {"eth_type":2048, "ip_proto":1}                # drop all ICMP
#   {"eth_type":2048, "ip_proto":6, "tcp_dst":22} # drop SSH
#   {"ipv4_src":"10.0.1.5", "ipv4_dst":"10.0.2.5"}  # host-to-host
# =============================================

import os
import json
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3

class SimpleFirewall(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SimpleFirewall, self).__init__(*args, **kwargs)
        self.drop_rules = self._load_rules()

    def _load_rules(self):
        # Defaults: block ICMP between any hosts, block SSH (22)
        defaults = [
            # Default: allow everything. Uncomment examples or supply FIREWALL_RULES to block.
            # {"eth_type": 0x0800, "ip_proto": 6, "tcp_dst": 22},  # block SSH
        ]
        path = os.environ.get('FIREWALL_RULES')
        if path and os.path.isfile(path):
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.logger.info("Loaded %d firewall rules from %s", len(data), path)
                    return data
            except Exception as e:
                self.logger.error("Failed to load rules from %s: %s", path, e)
        self.logger.info("Using default firewall rules (%d)", len(defaults))
        return defaults

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofp = datapath.ofproto
        parser = datapath.ofproto_parser
        # Install high-priority drop rules
        for i, match_kwargs in enumerate(self.drop_rules, start=1):
            try:
                match = parser.OFPMatch(**match_kwargs)
                # No actions => drop. Use CLEAR_ACTIONS instruction for clarity.
                inst = [parser.OFPInstructionActions(ofp.OFPIT_CLEAR_ACTIONS, [])]
                mod = parser.OFPFlowMod(datapath=datapath, priority=100, match=match, instructions=inst)
                datapath.send_msg(mod)
                self.logger.info("Firewall: installed drop rule %d on sw=%s match=%s", i, datapath.id, match_kwargs)
            except Exception as e:
                self.logger.error("Firewall: failed to install rule %s on sw=%s: %s", match_kwargs, datapath.id, e)
