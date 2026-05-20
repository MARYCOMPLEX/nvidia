#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import socket
import struct
import hashlib
import base64
import asyncio
import aiohttp
import logging
import ipaddress
import subprocess
import json
import time
from aiohttp import web

# ============================================================
# 环境变量 (保持原有, 修改默认值以隐藏特征)
# ============================================================
UUID = os.environ.get('UUID', '7bd180e8-1142-4387-93f5-03e8d750a896')
NEZHA_SERVER = os.environ.get('NEZHA_SERVER', '')
NEZHA_PORT = os.environ.get('NEZHA_PORT', '')
NEZHA_KEY = os.environ.get('NEZHA_KEY', '')
DOMAIN = os.environ.get('DOMAIN', '')

# 伪装路径: 订阅端点头像成 license 验证
SUB_PATH = os.environ.get('SUB_PATH', 'api/v1/license')
# 伪装路径: WebSocket 代理伪装成 AI streaming 端点
WSPATH = os.environ.get('WSPATH', 'ws/v1/completions')

NAME = os.environ.get('NAME', '')
PORT = int(os.environ.get('SERVER_PORT') or os.environ.get('PORT') or 3000)
AUTO_ACCESS = os.environ.get('AUTO_ACCESS', '').lower() == 'true'
DEBUG = os.environ.get('DEBUG', '').lower() == 'true'

# ============================================================
# 全局变量
# ============================================================
CurrentDomain = DOMAIN
CurrentPort = 443
Tls = 'tls'
ISP = ''
APP_START_TIME = time.time()

DNS_SERVERS = ['8.8.4.4', '1.1.1.1']
BLOCKED_DOMAINS = [
    'speedtest.net', 'fast.com', 'speedtest.cn', 'speed.cloudflare.com', 'speedof.me',
    'testmy.net', 'bandwidth.place', 'speed.io', 'librespeed.org', 'speedcheck.org'
]

# 日志级别
log_level = logging.DEBUG if DEBUG else logging.INFO
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logging.getLogger('aiohttp.access').setLevel(logging.WARNING)
logging.getLogger('aiohttp.server').setLevel(logging.WARNING)
logging.getLogger('aiohttp.client').setLevel(logging.WARNING)
logging.getLogger('aiohttp.internal').setLevel(logging.WARNING)
logging.getLogger('aiohttp.websocket').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ============================================================
# 原有工具函数 (保持不变)
# ============================================================

def is_port_available(port, host='0.0.0.0'):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False

def find_available_port(start_port, max_attempts=100):
    for port in range(start_port, start_port + max_attempts):
        if is_port_available(port):
            return port
    return None

def is_blocked_domain(host: str) -> bool:
    if not host:
        return False
    host_lower = host.lower()
    return any(host_lower == blocked or host_lower.endswith('.' + blocked)
              for blocked in BLOCKED_DOMAINS)

async def get_isp():
    global ISP
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://api.ip.sb/geoip',
                                 headers={'User-Agent': 'Mozilla/5.0'},
                                 timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ISP = f"{data.get('country_code', '')}-{data.get('isp', '')}".replace(' ', '_')
                    return
    except:
        pass
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('http://ip-api.com/json',
                                 headers={'User-Agent': 'Mozilla/5.0'},
                                 timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    ISP = f"{data.get('countryCode', '')}-{data.get('org', '')}".replace(' ', '_')
                    return
    except:
        pass
    ISP = 'Unknown'

async def get_ip():
    global CurrentDomain, Tls, CurrentPort
    if not DOMAIN or DOMAIN == 'your-domain.com':
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api-ipv4.ip.sb/ip', timeout=5) as resp:
                    if resp.status == 200:
                        ip = await resp.text()
                        CurrentDomain = ip.strip()
                        Tls = 'none'
                        CurrentPort = PORT
        except Exception as e:
            logger.error(f'Failed to get IP: {e}')
            CurrentDomain = 'change-your-domain.com'
            Tls = 'tls'
            CurrentPort = 443
    else:
        CurrentDomain = DOMAIN
        Tls = 'tls'
        CurrentPort = 443

async def resolve_host(host: str) -> str:
    try:
        ipaddress.ip_address(host)
        return host
    except:
        pass
    for dns_server in DNS_SERVERS:
        try:
            async with aiohttp.ClientSession() as session:
                url = f'https://dns.google/resolve?name={host}&type=A'
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('Status') == 0 and data.get('Answer'):
                            for answer in data['Answer']:
                                if answer.get('type') == 1:
                                    return answer.get('data')
        except:
            continue
    return host


# ============================================================
# ProxyHandler (原有代理逻辑, 保持不变)
# ============================================================

class ProxyHandler:
    def __init__(self, uuid: str):
        self.uuid = uuid
        self.uuid_bytes = bytes.fromhex(uuid)

    async def handle_vless(self, websocket, first_msg: bytes) -> bool:
        try:
            if len(first_msg) < 18 or first_msg[0] != 0:
                return False
            if first_msg[1:17] != self.uuid_bytes:
                return False
            i = first_msg[17] + 19
            if i + 3 > len(first_msg):
                return False
            port = struct.unpack('!H', first_msg[i:i+2])[0]
            i += 2
            atyp = first_msg[i]
            i += 1
            host = ''
            if atyp == 1:
                if i + 4 > len(first_msg):
                    return False
                host = '.'.join(str(b) for b in first_msg[i:i+4])
                i += 4
            elif atyp == 2:
                if i >= len(first_msg):
                    return False
                host_len = first_msg[i]
                i += 1
                if i + host_len > len(first_msg):
                    return False
                host = first_msg[i:i+host_len].decode()
                i += host_len
            elif atyp == 3:
                if i + 16 > len(first_msg):
                    return False
                host = ':'.join(f'{(first_msg[j] << 8) + first_msg[j+1]:04x}'
                              for j in range(i, i+16, 2))
                i += 16
            else:
                return False
            if is_blocked_domain(host):
                await websocket.close()
                return False
            await websocket.send_bytes(bytes([0, 0]))
            resolved_host = await resolve_host(host)
            try:
                reader, writer = await asyncio.open_connection(resolved_host, port)
                if i < len(first_msg):
                    writer.write(first_msg[i:])
                    await writer.drain()
                async def forward_ws_to_tcp():
                    try:
                        async for msg in websocket:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                writer.write(msg.data)
                                await writer.drain()
                    except:
                        pass
                    finally:
                        writer.close()
                        await writer.wait_closed()
                async def forward_tcp_to_ws():
                    try:
                        while True:
                            data = await reader.read(4096)
                            if not data:
                                break
                            await websocket.send_bytes(data)
                    except:
                        pass
                await asyncio.gather(forward_ws_to_tcp(), forward_tcp_to_ws())
            except Exception as e:
                if DEBUG:
                    logger.error(f"Connection error: {e}")
            return True
        except Exception as e:
            if DEBUG:
                logger.error(f"VLESS handler error: {e}")
            return False

    async def handle_trojan(self, websocket, first_msg: bytes) -> bool:
        try:
            if len(first_msg) < 58:
                return False
            received_hash_bytes = first_msg[:56]
            hash_obj1 = hashlib.sha224()
            hash_obj1.update(self.uuid.encode())
            expected_hash_hex1 = hash_obj1.hexdigest()
            standard_uuid = UUID
            hash_obj2 = hashlib.sha224()
            hash_obj2.update(standard_uuid.encode())
            expected_hash_hex2 = hash_obj2.hexdigest()
            received_hash_hex = received_hash_bytes.decode('ascii', errors='ignore')
            if received_hash_hex != expected_hash_hex1 and received_hash_hex != expected_hash_hex2:
                return False
            offset = 56
            if first_msg[offset:offset+2] == b'\r\n':
                offset += 2
            cmd = first_msg[offset]
            if cmd != 1:
                return False
            offset += 1
            atyp = first_msg[offset]
            offset += 1
            host = ''
            if atyp == 1:
                host = '.'.join(str(b) for b in first_msg[offset:offset+4])
                offset += 4
            elif atyp == 3:
                host_len = first_msg[offset]
                offset += 1
                host = first_msg[offset:offset+host_len].decode()
                offset += host_len
            elif atyp == 4:
                host = ':'.join(f'{(first_msg[j] << 8) + first_msg[j+1]:04x}'
                              for j in range(offset, offset+16, 2))
                offset += 16
            else:
                return False
            port = struct.unpack('!H', first_msg[offset:offset+2])[0]
            offset += 2
            if first_msg[offset:offset+2] == b'\r\n':
                offset += 2
            if is_blocked_domain(host):
                await websocket.close()
                return False
            resolved_host = await resolve_host(host)
            try:
                reader, writer = await asyncio.open_connection(resolved_host, port)
                if offset < len(first_msg):
                    writer.write(first_msg[offset:])
                    await writer.drain()
                async def forward_ws_to_tcp():
                    try:
                        async for msg in websocket:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                writer.write(msg.data)
                                await writer.drain()
                    except:
                        pass
                    finally:
                        writer.close()
                        await writer.wait_closed()
                async def forward_tcp_to_ws():
                    try:
                        while True:
                            data = await reader.read(4096)
                            if not data:
                                break
                            await websocket.send_bytes(data)
                    except:
                        pass
                await asyncio.gather(forward_ws_to_tcp(), forward_tcp_to_ws())
            except Exception as e:
                if DEBUG:
                    logger.error(f"Connection error: {e}")
            return True
        except Exception as e:
            if DEBUG:
                logger.error(f"Tro handler error: {e}")
            return False

    async def handle_shadowsocks(self, websocket, first_msg: bytes) -> bool:
        try:
            if len(first_msg) < 7:
                return False
            offset = 0
            atyp = first_msg[offset]
            offset += 1
            host = ''
            if atyp == 1:
                if offset + 4 > len(first_msg):
                    return False
                host = '.'.join(str(b) for b in first_msg[offset:offset+4])
                offset += 4
            elif atyp == 3:
                if offset >= len(first_msg):
                    return False
                host_len = first_msg[offset]
                offset += 1
                if offset + host_len > len(first_msg):
                    return False
                host = first_msg[offset:offset+host_len].decode()
                offset += host_len
            elif atyp == 4:
                if offset + 16 > len(first_msg):
                    return False
                host = ':'.join(f'{(first_msg[j] << 8) + first_msg[j+1]:04x}'
                              for j in range(offset, offset+16, 2))
                offset += 16
            else:
                return False
            if offset + 2 > len(first_msg):
                return False
            port = struct.unpack('!H', first_msg[offset:offset+2])[0]
            offset += 2
            if is_blocked_domain(host):
                await websocket.close()
                return False
            resolved_host = await resolve_host(host)
            try:
                reader, writer = await asyncio.open_connection(resolved_host, port)
                if offset < len(first_msg):
                    writer.write(first_msg[offset:])
                    await writer.drain()
                async def forward_ws_to_tcp():
                    try:
                        async for msg in websocket:
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                writer.write(msg.data)
                                await writer.drain()
                    except:
                        pass
                    finally:
                        writer.close()
                        await writer.wait_closed()
                async def forward_tcp_to_ws():
                    try:
                        while True:
                            data = await reader.read(4096)
                            if not data:
                                break
                            await websocket.send_bytes(data)
                    except:
                        pass
                await asyncio.gather(forward_ws_to_tcp(), forward_tcp_to_ws())
            except Exception as e:
                if DEBUG:
                    logger.error(f"Connection error: {e}")
            return True
        except Exception as e:
            if DEBUG:
                logger.error(f"Shadowsocks handler error: {e}")
            return False


# ============================================================
# WebSocket 代理处理 (原有逻辑, 加上了伪装路径匹配)
# ============================================================

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    CUUID = UUID.replace('-', '')
    path = request.path

    # 匹配伪装后的 WebSocket 路径
    if f'/{WSPATH}' not in path:
        await ws.close()
        return ws

    proxy = ProxyHandler(CUUID)

    try:
        first_msg = await asyncio.wait_for(ws.receive(), timeout=5)
        if first_msg.type != aiohttp.WSMsgType.BINARY:
            await ws.close()
            return ws
        msg_data = first_msg.data

        if len(msg_data) > 17 and msg_data[0] == 0:
            if await proxy.handle_vless(ws, msg_data):
                return ws
        if len(msg_data) >= 58:
            if await proxy.handle_trojan(ws, msg_data):
                return ws
        if len(msg_data) > 0 and msg_data[0] in (1, 3, 4):
            if await proxy.handle_shadowsocks(ws, msg_data):
                return ws
        await ws.close()
    except asyncio.TimeoutError:
        await ws.close()
    except Exception as e:
        if DEBUG:
            logger.error(f"WebSocket handler error: {e}")
        await ws.close()
    return ws


# ============================================================
# 伪装层: AI API 端点
# ============================================================

MODELS = [
    {"id": "gpt-4o", "object": "model", "created": 1700000000, "owned_by": "system"},
    {"id": "gpt-4o-mini", "object": "model", "created": 1700000001, "owned_by": "system"},
    {"id": "claude-3-opus-20240229", "object": "model", "created": 1700000002, "owned_by": "system"},
    {"id": "claude-3-sonnet-20240229", "object": "model", "created": 1700000003, "owned_by": "system"},
    {"id": "gpt-4-turbo", "object": "model", "created": 1700000004, "owned_by": "system"},
    {"id": "gpt-3.5-turbo", "object": "model", "created": 1700000005, "owned_by": "system"},
    {"id": "dall-e-3", "object": "model", "created": 1700000006, "owned_by": "system"},
    {"id": "text-embedding-3-large", "object": "model", "created": 1700000007, "owned_by": "system"},
]

MOCK_CHAT_RESPONSE = {
    "id": "chatcmpl-9a8b7c6d5e4f3a2b1c0d9e8f",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "gpt-4o",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Hello! I'm the AI assistant. How can I help you today?"
            },
            "finish_reason": "stop"
        }
    ],
    "usage": {
        "prompt_tokens": 25,
        "completion_tokens": 13,
        "total_tokens": 38
    }
}

MOCK_IMAGE_RESPONSE = {
    "created": 1700000000,
    "data": [
        {
            "url": "https://example.com/images/generated/abc123.png",
            "revised_prompt": "A beautiful landscape"
        }
    ]
}

async def handle_root(request):
    """AI API 首页"""
    accept = request.headers.get('Accept', '')
    if 'application/json' in accept:
        return web.json_response({
            "status": "operational",
            "service": "AI Inference API",
            "version": "v1",
            "docs": "/docs",
            "uptime_seconds": int(time.time() - APP_START_TIME)
        })
    try:
        with open('index.html', 'r', encoding='utf-8') as f:
            content = f.read()
        return web.Response(text=content, content_type='text/html')
    except:
        return web.json_response({
            "name": "AI Inference API",
            "version": "v1.0.0",
            "status": "operational"
        })

async def handle_models(request):
    """GET /api/v1/models — 返回模型列表 (模仿 OpenAI API)"""
    return web.json_response({
        "object": "list",
        "data": MODELS
    })

async def handle_chat_completions(request):
    """POST /api/v1/chat/completions — 返回 mock 聊天完成"""
    try:
        body = await request.json()
    except:
        body = {}
    model = body.get('model', 'gpt-4o')
    stream = body.get('stream', False)

    resp = dict(MOCK_CHAT_RESPONSE)
    resp['model'] = model
    resp['created'] = int(time.time())
    resp['id'] = f"chatcmpl-{base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip('=')}"

    # 支持流式响应 (验证 WebSocket 合理性)
    if stream:
        resp['choices'][0]['delta'] = {"role": "assistant", "content": ""}
        resp['choices'][0].pop('message', None)
        data = f"data: {json.dumps(resp)}\n\n"
        return web.Response(text=data, content_type='text/event-stream')
    return web.json_response(resp)

async def handle_image_generations(request):
    """POST /api/v1/images/generations — 返回 mock 图片生成"""
    resp = dict(MOCK_IMAGE_RESPONSE)
    resp['created'] = int(time.time())
    return web.json_response(resp)

async def handle_robots_txt(request):
    """robots.txt 允许所有爬虫"""
    content = "User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap.xml\n"
    return web.Response(text=content, content_type='text/plain')

async def handle_api_health(request):
    """GET /api/v1/health — 健康检查"""
    return web.json_response({
        "status": "healthy",
        "service": "ai-inference-api",
        "version": "1.0.0",
        "uptime_seconds": int(time.time() - APP_START_TIME),
        "models_available": len(MODELS),
        "requests_total": 0
    })


# ============================================================
# 伪装层: 订阅端点 (原来 /sub, 现在伪装成 license/verify)
# ============================================================

async def http_handler(request):
    path = request.path.rstrip('/')

    # ========== 伪装 API 路由 ==========
    if path == '/':
        return await handle_root(request)

    if path == '/api/v1/models':
        return await handle_models(request)

    if path == '/api/v1/chat/completions':
        return await handle_chat_completions(request)

    if path == '/api/v1/images/generations':
        return await handle_image_generations(request)

    if path == '/robots.txt':
        return await handle_robots_txt(request)

    if path == '/api/v1/health':
        return await handle_api_health(request)

    # ========== 订阅端点 (伪装成 license verify) ==========
    if path == f'/{SUB_PATH}':
        raw_mode = request.query.get('raw', '').lower() in ('true', '1', 'yes')
        await get_isp()
        await get_ip()

        name_part = f"{NAME}-{ISP}" if NAME else ISP
        tls_param = 'tls' if Tls == 'tls' else 'none'
        ss_tls_param = 'tls;' if Tls == 'tls' else ''

        vless_url = f"vless://{UUID}@{CurrentDomain}:{CurrentPort}?encryption=none&security={tls_param}&sni={CurrentDomain}&fp=chrome&type=ws&host={CurrentDomain}&path=%2F{WSPATH}#{name_part}"
        trojan_url = f"trojan://{UUID}@{CurrentDomain}:{CurrentPort}?security={tls_param}&sni={CurrentDomain}&fp=chrome&type=ws&host={CurrentDomain}&path=%2F{WSPATH}#{name_part}"
        ss_method_password = base64.b64encode(f"none:{UUID}".encode()).decode()
        ss_url = f"ss://{ss_method_password}@{CurrentDomain}:{CurrentPort}?plugin=v2ray-plugin;mode%3Dwebsocket;host%3D{CurrentDomain};path%3D%2F{WSPATH};{ss_tls_param}sni%3D{CurrentDomain};skip-cert-verify%3Dtrue;mux%3D0#{name_part}"

        subscription = f"{vless_url}\n{trojan_url}\n{ss_url}"
        base64_content = base64.b64encode(subscription.encode()).decode()

        # raw 模式: 返回原始 base64 (兼容 Clash Verge 等客户端)
        if raw_mode:
            return web.Response(text=base64_content + '\n', content_type='text/plain')

        # ===== 伪装返回: 用 JSON 包裹订阅内容, 看起来像 license 验证 =====
        client_ip = request.remote or request.headers.get('X-Forwarded-For', 'unknown')
        return web.json_response({
            "success": True,
            "message": "License verified successfully",
            "data": {
                "license_key": UUID,
                "client_ip": client_ip,
                "client_config": base64_content,
                "expires_at": "2026-12-31T23:59:59Z",
                "quota_remaining": 1024 * 1024 * 1024 * 10,  # 10GB
                "plan": "enterprise"
            }
        })

    # ========== 404: 返回 API 风格错误 ==========
    return web.json_response({
        "error": {
            "message": f"Endpoint not found: {path}",
            "type": "invalid_request_error",
            "code": "endpoint_not_found"
        }
    }, status=404)


# ============================================================
# Nezha 监控 (保持不变)
# ============================================================

def get_download_url():
    import platform
    arch = platform.machine()
    if 'arm' in arch.lower() or 'aarch64' in arch.lower():
        if not NEZHA_PORT:
            return 'https://arm64.eooce.com/v1'
        else:
            return 'https://arm64.eooce.com/agent'
    else:
        if not NEZHA_PORT:
            return 'https://amd64.eooce.com/v1'
        else:
            return 'https://amd64.eooce.com/agent'

async def download_file():
    if not NEZHA_SERVER and not NEZHA_KEY:
        return
    try:
        url = get_download_url()
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    with open('npm', 'wb') as f:
                        f.write(content)
                    os.chmod('npm', 0o755)
                    logger.info('✅ npm downloaded successfully')
    except Exception as e:
        logger.error(f'Download failed: {e}')

async def run_nezha():
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
        if './npm' in result.stdout and '[n]pm' in result.stdout:
            logger.info('npm is already running, skip...')
            return
    except:
        pass
    await download_file()
    command = ''
    tls_ports = ['443', '8443', '2096', '2087', '2083', '2053']
    if NEZHA_SERVER and NEZHA_PORT and NEZHA_KEY:
        nezha_tls = '--tls' if NEZHA_PORT in tls_ports else ''
        command = f'nohup ./npm -s {NEZHA_SERVER}:{NEZHA_PORT} -p {NEZHA_KEY} {nezha_tls} --disable-auto-update --report-delay 4 --skip-conn --skip-procs >/dev/null 2>&1 &'
    elif NEZHA_SERVER and NEZHA_KEY:
        if not NEZHA_PORT:
            port = NEZHA_SERVER.split(':')[-1] if ':' in NEZHA_SERVER else ''
            nz_tls = 'true' if port in tls_ports else 'false'
            config = f"""client_secret: {NEZHA_KEY}
debug: false
disable_auto_update: true
disable_command_execute: false
disable_force_update: true
disable_nat: false
disable_send_query: false
gpu: false
insecure_tls: true
ip_report_period: 1800
report_delay: 4
server: {NEZHA_SERVER}
skip_connection_count: true
skip_procs_count: true
temperature: false
tls: {nz_tls}
use_gitee_to_upgrade: false
use_ipv6_country_code: false
uuid: {UUID}"""
            with open('config.yaml', 'w') as f:
                f.write(config)
        command = f'nohup ./npm -c config.yaml >/dev/null 2>&1 &'
    else:
        return
    try:
        subprocess.Popen(command, shell=True, executable='/bin/bash')
        logger.info('✅ nz started successfully')
    except Exception as e:
        logger.error(f'Error running nz: {e}')

async def add_access_task():
    if not AUTO_ACCESS or not DOMAIN:
        return
    full_url = f"https://{DOMAIN}/{SUB_PATH}"
    try:
        async with aiohttp.ClientSession() as session:
            await session.post("https://oooo.serv00.net/add-url",
                             json={"url": full_url},
                             headers={'Content-Type': 'application/json'})
        logger.info('Automatic Access Task added successfully')
    except:
        pass

def cleanup_files():
    for file in ['npm', 'config.yaml']:
        try:
            if os.path.exists(file):
                os.remove(file)
        except:
            pass


# ============================================================
# 主入口
# ============================================================

async def main():
    actual_port = PORT
    if not is_port_available(actual_port):
        logger.warning(f"Port {actual_port} is already in use, finding available port...")
        new_port = find_available_port(actual_port + 1)
        if new_port:
            actual_port = new_port
            logger.info(f"Using port {actual_port} instead of {PORT}")
        else:
            logger.error("No available ports found")
            sys.exit(1)

    app = web.Application()

    # 伪装路由: AI API 端点
    app.router.add_get('/', handle_root)
    app.router.add_get('/api/v1/models', handle_models)
    app.router.add_post('/api/v1/chat/completions', handle_chat_completions)
    app.router.add_post('/api/v1/images/generations', handle_image_generations)
    app.router.add_get('/robots.txt', handle_robots_txt)
    app.router.add_get('/api/v1/health', handle_api_health)

    # 隐藏路由: 订阅 (伪装为 license verify)
    app.router.add_get(f'/{SUB_PATH}', http_handler)

    # 代理 WebSocket
    app.router.add_get(f'/{WSPATH}', websocket_handler)
    app.router.add_get(f'/ws/v1/completions', websocket_handler)  # 别名, 增加迷惑性

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', actual_port)
    await site.start()
    logger.info(f"✅ AI Inference API server is running on port {actual_port}")
    logger.info(f"   License endpoint: /{SUB_PATH}")
    logger.info(f"   WS proxy path: /{WSPATH}")

    asyncio.create_task(run_nezha())

    async def delayed_cleanup():
        await asyncio.sleep(180)
        cleanup_files()
    asyncio.create_task(delayed_cleanup())
    await add_access_task()

    try:
        await asyncio.Future()
    except KeyboardInterrupt:
        pass
    finally:
        await runner.cleanup()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nServer stopped by user")
        cleanup_files()
