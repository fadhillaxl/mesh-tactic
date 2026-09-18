"""
Tactical SDR Mesh gRPC CLI Client.
Queries remote node status, telemetry, neighbors, ping diagnostics, chat, and spectrum.
"""

import sys
import time
import argparse
import grpc
import numpy as np

from ..generated import tsm_pb2, tsm_pb2_grpc


def print_header(title: str):
    print("=" * 68)
    print(f"  {title}")
    print("=" * 68)


def cmd_status(stub_node, stub_sdr):
    print_header("TACTICAL NODE & SDR STATUS")
    info = stub_node.GetNodeInfo(tsm_pb2.NodeInfoRequest())
    print(f" Node Callsign:    {info.node_id} ({info.hostname})")
    print(f" Mesh IP (bat0):   {info.mesh_ip}")
    print(f" TAP Interface:    MAC={info.tap_mac} | MTU={info.tap_mtu}B")
    print(f" Uptime:           {info.uptime_sec:.1f}s")
    print(f" Memory:           {info.mem_used_bytes // (1024*1024)} MB / {info.mem_total_bytes // (1024*1024)} MB")
    print(f" B.A.T.M.A.N.-adv: Active={info.is_mesh_active} ({info.batman_version})")

    sdr = stub_node.GetSDRTelemetry(tsm_pb2.SDRTelemetryRequest())
    print("\n--- Physical RF & SDR Hardware ---")
    print(f" Transceiver:      {'CONNECTED (' + sdr.sdr_uri + ')' if sdr.sdr_connected else 'PEER / SYNTHETIC MODE'}")
    print(f" Carrier LO:       {sdr.center_freq_hz / 1e6:.3f} MHz (Locked: {sdr.is_lo_locked})")
    print(f" Sample Rate:      {sdr.sample_rate_sps / 1e6:.2f} MSps | Filter: {sdr.rf_bandwidth_hz / 1e6:.2f} MHz")
    print(f" Hardware Gains:   Rx={sdr.rx_gain_db:.1f} dB | Tx={sdr.tx_atten_db:.1f} dB")
    print(f" Channel Energy:   Live RSSI = {sdr.live_rssi_db:.1f} dB")
    print(f" FPGA Core XADC:   Temp = {sdr.fpga_temp_c:.1f} °C | VccInt = {sdr.vccint_v:.2f} V")


def cmd_neighbors(stub_node):
    print_header("B.A.T.M.A.N.-ADV MESH TOPOLOGY & NEIGHBORS")
    res = stub_node.GetMeshNeighbors(tsm_pb2.MeshNeighborsRequest())
    if not res.neighbors:
        print("  No neighbors detected in table yet. Is orchestrator running?")
        return

    print(f" Total Discovered Neighbors: {res.total_neighbors}\n")
    print(f" {'INTERFACE':<12} {'MAC ADDRESS':<20} {'TQ METRIC':<12} {'LAST SEEN':<12} {'NEXTHOP'}")
    print("-" * 68)
    for n in res.neighbors:
        tq_pct = round((n.tq_metric / 255.0) * 100)
        tq_display = f"{n.tq_metric}/255 ({tq_pct}%)"
        nh_str = "YES (*)" if n.is_nexthop else "NO"
        print(f" {n.interface:<12} {n.mac_address:<20} {tq_display:<12} {n.last_seen_sec:<10.1f}s  {nh_str}")


def cmd_ping(stub_node, target_ip: str, count: int = 3):
    print_header(f"MESH ICMP PING DIAGNOSTIC -> {target_ip}")
    res = stub_node.ExecutePing(tsm_pb2.PingRequest(target_ip=target_ip, count=count))
    print(f" Target IP:     {res.target_ip}")
    print(f" Packets Sent:  {res.packets_sent} | Received: {res.packets_recv} | Loss: {res.packet_loss_pct:.1f}%")
    if res.is_success:
        print(f" Latency RTT:   Min={res.rtt_min_ms:.2f}ms | Avg={res.rtt_avg_ms:.2f}ms | Max={res.rtt_max_ms:.2f}ms")
        print(" Status:        [PASS] Link is active and operational.")
    else:
        print(" Status:        [FAIL] Destination unreachable.")


def cmd_send_chat(stub_chat, target_ip: str, text: str):
    res = stub_chat.SendMessage(tsm_pb2.ChatMessageRequest(target_ip=target_ip, text=text))
    print(f"[SENT] Message ID: {res.message_id} | Delivered={res.delivered} | Target={target_ip}")


def cmd_stream_chat(stub_chat):
    print_header("REAL-TIME TACTICAL CHAT STREAM (Ctrl+C to exit)")
    try:
        for msg in stub_chat.StreamMessages(tsm_pb2.StreamMessagesRequest()):
            ts = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
            print(f"[{ts}] [{msg.sender_callsign} ({msg.sender_ip}) -> {msg.target_ip}]: {msg.text}")
    except KeyboardInterrupt:
        print("\n[INFO] Stopped chat stream.")


def cmd_spectrum(stub_spec):
    print_header("ASCII RF SPECTRUM SCAN (915.000 MHz)")
    scan = stub_spec.GetSpectrumScan(tsm_pb2.SpectrumRequest()).scan
    powers = np.frombuffer(scan.powers_uint8, dtype=np.uint8)

    # Subsample to 64 columns for terminal display
    chunk_size = len(powers) // 64
    compact = [int(np.mean(powers[i*chunk_size:(i+1)*chunk_size])) for i in range(64)]

    # Draw 10 vertical rows
    height = 10
    print(f" Center: {scan.center_freq_hz/1e6:.3f} MHz | Span: {scan.span_hz/1e6:.1f} MHz | Peak: {scan.peak_power_dbm:.1f} dBm @ {scan.peak_freq_hz/1e6:.4f} MHz\n")
    for row in range(height, 0, -1):
        threshold = int((row / height) * 255)
        line = "".join("█" if val >= threshold else " " for val in compact)
        dbm = int(-110 + (row / height) * 105)
        print(f" {dbm:>4} dBm | {line}")
    print(" " * 10 + "+" + "-" * 64)
    print(" " * 11 + "914.5 MHz" + " " * 22 + "915.0 MHz" + " " * 20 + "915.5 MHz")


def main():
    parser = argparse.ArgumentParser(description="Tactical SDR Mesh gRPC CLI Client")
    parser.add_argument("--host", default="localhost", help="Target node hostname/IP (default: localhost)")
    parser.add_argument("--port", type=int, default=50051, help="gRPC port (default: 50051)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Get Node and SDR hardware status")
    subparsers.add_parser("neighbors", help="Get B.A.T.M.A.N.-adv mesh topology")

    ping_p = subparsers.add_parser("ping", help="Ping remote mesh IP")
    ping_p.add_argument("--target", default="10.10.0.2", help="Target IP")
    ping_p.add_argument("--count", type=int, default=3, help="Ping packet count")

    chat_p = subparsers.add_parser("chat", help="Send or stream tactical chat")
    chat_p.add_argument("--send", type=str, default=None, help="Text to send")
    chat_p.add_argument("--target", type=str, default="10.10.0.2", help="Target IP")
    chat_p.add_argument("--stream", action="store_true", help="Listen for incoming messages")

    subparsers.add_parser("spectrum", help="View RF baseband spectrum scan")

    args = parser.parse_args()

    channel = grpc.insecure_channel(f"{args.host}:{args.port}")
    stub_node = tsm_pb2_grpc.TacticalNodeServiceStub(channel)
    stub_chat = tsm_pb2_grpc.TacticalChatServiceStub(channel)
    stub_spec = tsm_pb2_grpc.TacticalSpectrumServiceStub(channel)

    if args.command == "status":
        cmd_status(stub_node, stub_node)
    elif args.command == "neighbors":
        cmd_neighbors(stub_node)
    elif args.command == "ping":
        cmd_ping(stub_node, args.target, args.count)
    elif args.command == "chat":
        if args.send:
            cmd_send_chat(stub_chat, args.target, args.send)
        elif args.stream:
            cmd_stream_chat(stub_chat)
        else:
            print("Specify --send <text> or --stream")
    elif args.command == "spectrum":
        cmd_spectrum(stub_spec)


if __name__ == "__main__":
    main()
