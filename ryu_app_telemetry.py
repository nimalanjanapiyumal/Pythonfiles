
# =============================================
# ryu_app_telemetry.py
# ---------------------------------------------
# Periodic telemetry collector for OpenFlow 1.3 (OVS/Mininet).
# - Polls port/flow stats from all connected switches every N seconds
# - Computes per-port throughput (bps) and packet rate (pps)
# - Writes CSV logs under ./logs/
# - Safe to run alongside the other apps
# =============================================

import os
import csv
import time
from collections import defaultdict

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.controller import dpset
from ryu.controller import handler
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

class Telemetry13(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(Telemetry13, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.poll_interval = float(os.environ.get('POLL_INTERVAL', '2.0'))
        self.logs_dir = os.path.abspath(os.environ.get('TELEM_LOG_DIR', './logs'))
        os.makedirs(self.logs_dir, exist_ok=True)
        # state: previous counters to compute rates
        self.prev_port = {}  # {dpid: {port_no: (ts, rx_bytes, tx_bytes, rx_pkts, tx_pkts)}}
        self.monitor_thread = hub.spawn(self._monitor)

    @set_ev_cls(dpset.EventDP, handler.MAIN_DISPATCHER)
    def _handler_dp(self, ev):
        dp = ev.dp
        if ev.enter:
            self.logger.info('Telemetry: switch joined dpid=%s', dp.id)
            self.datapaths[dp.id] = dp
        else:
            self.logger.info('Telemetry: switch left dpid=%s', dp.id)
            self.datapaths.pop(dp.id, None)
            self.prev_port.pop(dp.id, None)

    def _monitor(self):
        while True:
            for dpid, dp in list(self.datapaths.items()):
                parser = dp.ofproto_parser
                ofp = dp.ofproto
                try:
                    # Port stats request
                    req = parser.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY)
                    dp.send_msg(req)
                    # Flow stats request (optional; aggregated further below)
                    reqf = parser.OFPFlowStatsRequest(dp, 0, ofp.OFPTT_ALL, ofp.OFPP_ANY, ofp.OFPG_ANY)
                    dp.send_msg(reqf)
                except Exception as e:
                    self.logger.error('Telemetry: request failed for dpid=%s: %s', dpid, e)
            hub.sleep(self.poll_interval)

    # ---- Port stats reply ----
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        dp = ev.msg.datapath
        dpid = dp.id
        now = time.time()
        rows = []
        prev = self.prev_port.setdefault(dpid, {})
        for stat in ev.msg.body:
            port_no = stat.port_no
            if port_no >= dp.ofproto.OFPP_MAX:  # skip local/invalid
                continue
            rx_bytes = stat.rx_bytes
            tx_bytes = stat.tx_bytes
            rx_pkts = stat.rx_packets
            tx_pkts = stat.tx_packets

            bps_rx = bps_tx = pps_rx = pps_tx = 0.0
            if port_no in prev:
                ts0, rx0, tx0, rpk0, tpk0 = prev[port_no]
                dt = max(1e-9, now - ts0)
                bps_rx = 8.0 * (rx_bytes - rx0) / dt
                bps_tx = 8.0 * (tx_bytes - tx0) / dt
                pps_rx = (rx_pkts - rpk0) / dt
                pps_tx = (tx_pkts - tpk0) / dt
            prev[port_no] = (now, rx_bytes, tx_bytes, rx_pkts, tx_pkts)

            rows.append({
                'timestamp': now,
                'dpid': dpid,
                'port': port_no,
                'rx_bytes': rx_bytes,
                'tx_bytes': tx_bytes,
                'rx_pkts': rx_pkts,
                'tx_pkts': tx_pkts,
                'rx_bps': round(bps_rx, 2),
                'tx_bps': round(bps_tx, 2),
                'rx_pps': round(pps_rx, 2),
                'tx_pps': round(pps_tx, 2),
            })
        if rows:
            self._append_csv(os.path.join(self.logs_dir, f'port_stats_{dpid}.csv'), rows,
                             header=['timestamp','dpid','port','rx_bytes','tx_bytes','rx_pkts','tx_pkts','rx_bps','tx_bps','rx_pps','tx_pps'])

    # ---- Flow stats reply ----
    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        dp = ev.msg.datapath
        dpid = dp.id
        now = time.time()
        rows = []
        for stat in ev.msg.body:
            # Only log non-table-miss flows (priority > 0) to reduce noise
            if getattr(stat, 'priority', 0) == 0:
                continue
            # Extract key fields for quick analysis
            cookie = getattr(stat, 'cookie', 0)
            prio = getattr(stat, 'priority', 0)
            pkt_count = getattr(stat, 'packet_count', 0)
            byte_count = getattr(stat, 'byte_count', 0)
            match = getattr(stat, 'match', None)
            match_dict = match.to_jsondict()['OFPMatch']['oxm_fields'] if match else []
            rows.append({
                'timestamp': now,
                'dpid': dpid,
                'cookie': cookie,
                'priority': prio,
                'packet_count': pkt_count,
                'byte_count': byte_count,
                'match': match_dict,
            })
        if rows:
            self._append_csv(os.path.join(self.logs_dir, f'flow_stats_{dpid}.csv'), rows,
                             header=['timestamp','dpid','cookie','priority','packet_count','byte_count','match'])

    def _append_csv(self, path, rows, header):
        exists = os.path.exists(path)
        with open(path, 'a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=header)
            if not exists:
                w.writeheader()
            for r in rows:
                w.writerow(r)

# ---------------------------------------------
# USAGE
#   POLL_INTERVAL=2 TELEM_LOG_DIR=./logs \
#   python -m ryu.cmd.manager --ofp-tcp-listen-port 6653 \
#     ryu_app_learning_switch.py ryu_app_firewall.py ryu_app_load_balancer.py ryu_app_telemetry.py
# Outputs CSVs like ./logs/port_stats_<dpid>.csv and flow_stats_<dpid>.csv
# ---------------------------------------------

