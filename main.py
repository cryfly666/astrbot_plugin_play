from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.platform import PlatformAdapterType
import asyncio
import aiohttp
import time

@register("minecraft_monitor", "YourName", "Minecraft服务器监控插件", "2.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}
        self.task = None
        
        # 配置处理
        self.target_group = self.config.get("target_group")
        if self.target_group and not str(self.target_group).isdigit():
            logger.error(f"target_group '{self.target_group}' 不是有效数字")
            self.target_group = None

        self.server_name = self.config.get("server_name", "Minecraft服务器")
        self.server_ip = self.config.get("server_ip")
        self.server_port = self.config.get("server_port")
        
        # 服务器类型标准化
        stype_raw = str(self.config.get("server_type", "je")).lower()
        self.server_type = "be" if stype_raw in ["be", "pe", "bedrock"] else "je"
        
        self.check_interval = int(self.config.get("check_interval", 10))
        self.enable_auto_monitor = self.config.get("enable_auto_monitor", False)
        
        # 缓存数据
        self.last_player_count = None
        self.last_player_list = []
        
        if not self.target_group or not self.server_ip or not self.server_port:
            logger.error("配置不完整(target_group/ip/port)，监控无法启动")
            self.enable_auto_monitor = False
        else:
            logger.info(f"MC监控已加载 | 服务器: {self.server_ip}:{self.server_port} ({self.server_type.upper()})")
        
        if self.enable_auto_monitor:
            asyncio.create_task(self._delayed_auto_start())

    async def _delayed_auto_start(self):
        await asyncio.sleep(5)
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.monitor_task())
            logger.info("🚀 自动启动服务器监控任务")

    async def get_hitokoto(self):
        """获取一言"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://v1.hitokoto.cn/?encode=text", timeout=2) as resp:
                    return await resp.text() if resp.status == 200 else None
        except:
            return None

    def _parse_players(self, players_data):
        """统一解析玩家列表，返回名字列表"""
        names = []
        if not players_data:
            return names
            
        # 兼容字符串格式 "A, B, C"
        if isinstance(players_data, str):
            return [n.strip() for n in players_data.split(",") if n.strip()]
            
        # 兼容列表格式
        if isinstance(players_data, list):
            for p in players_data:
                if isinstance(p, dict):
                    # 尝试获取各种可能的名称字段
                    name = p.get("name") or p.get("username") or p.get("name_clean") or p.get("xuid")
                    if name: names.append(str(name))
                else:
                    names.append(str(p))
        return names

    async def _fetch_server_data(self):
        """获取数据，增加防缓存机制"""
        if not self.server_ip or not self.server_port: return None
        
        # 增加时间戳参数防止CDN缓存
        ts = int(time.time())
        url = f"https://motd.minebbs.com/api/status?ip={self.server_ip}&port={self.server_port}&stype={self.server_type}&_={ts}"
        
        # 伪装成浏览器
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Cache-Control": "no-cache"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as response:
                    if response.status != 200:
                        logger.warning(f"API请求失败: {response.status}")
                        return None
                        
                    data = await response.json()
                    # logger.debug(f"API数据: {data}") # 调试时可开启

                    # 解析基础信息
                    status = data.get('status', 'offline')
                    version = data.get('version') or '未知版本'
                    motd = data.get('motd', '')
                    
                    # 提取MOTD纯文本
                    if isinstance(motd, dict):
                        motd = ' '.join(map(str, motd.get('clean', [])))
                    
                    # 解析玩家信息 (重点优化部分)
                    p_info = data.get('players', {})
                    # 某些基岩版API直接返回数字或None，统一转字典处理
                    if not isinstance(p_info, dict):
                        p_info = {'online': 0, 'max': 0, 'sample': []}

                    online = int(p_info.get('online', 0) or 0)
                    max_p = int(p_info.get('max', 0) or 0)
                    
                    # 智能查找玩家列表字段
                    sample = (p_info.get('sample') or p_info.get('list') or [])
                    
                    # 提取具体玩家名
                    player_names = self._parse_players(sample)

                    return {
                        'status': status,
                        'name': data.get('hostname') or self.server_name,
                        'version': version,
                        'online': online,
                        'max': max_p,
                        'player_names': player_names,
                        'motd': str(motd)
                    }
        except Exception as e:
            logger.error(f"获取服务器信息出错: {e}")
            return None

    def _format_msg(self, data):
        if not data: return "❌ 无法连接到监控API"
        
        emoji = "🟢" if data['status'] == "online" else "🔴"
        msg = [f"{emoji} {data['name']}"]
        
        if data['motd']:
            msg.append(f"📝 {data['motd']}")
            
        msg.append(f"🎮 {data['version']}")
        msg.append(f"👥 在线: {data['online']}/{data['max']}")
        
        if data['player_names']:
            names = data['player_names']
            p_str = ", ".join(names[:10])
            if len(names) > 10: p_str += f" 等{len(names)}人"
            msg.append(f"📋 列表: {p_str}")
            
        return "\n".join(msg)

    async def monitor_task(self):
        """定时监控核心逻辑"""
        while True:
            try:
                data = await self._fetch_server_data()
                
                if data and data['status'] == 'online':
                    curr_online = data['online']
                    curr_players = set(data['player_names'])
                    
                    # 首次运行初始化
                    if self.last_player_count is None:
                        self.last_player_count = curr_online
                        self.last_player_list = curr_players
                        logger.info(f"监控初始化完成，当前在线: {curr_online}")
                    else:
                        # 检测变化
                        changes = []
                        last_players = self.last_player_list
                        
                        joined = curr_players - last_players
                        left = last_players - curr_players
                        
                        if joined:
                            changes.append(f"📈 {', '.join(joined)} 加入了服务器")
                        if left:
                            changes.append(f"📉 {', '.join(left)} 离开了服务器")
                            
                        # 如果只有数量变化但获取不到具体名单（部分服务端特性）
                        if not joined and not left and curr_online != self.last_player_count:
                            diff = curr_online - self.last_player_count
                            symbol = "📈" if diff > 0 else "📉"
                            changes.append(f"{symbol} 在线人数变化: {diff:+d} (当前 {curr_online}人)")

                        if changes:
                            logger.info(f"检测到变化: {changes}")
                            # 构建完整消息
                            notify_msg = "🔔 状态变动:\n" + "\n".join(changes)
                            notify_msg += f"\n\n{self._format_msg(data)}"
                            
                            hito = await self.get_hitokoto()
                            if hito: notify_msg += f"\n\n💬 {hito}"
                            
                            await self.send_group_msg(notify_msg)
                        
                        # 更新缓存
                        self.last_player_count = curr_online
                        self.last_player_list = curr_players
                
                elif data is None:
                    # 获取失败时暂不处理，避免断网刷屏，仅日志
                    logger.debug("获取服务器数据失败")
                
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"监控循环异常: {e}")
                await asyncio.sleep(5)

    async def send_group_msg(self, text):
        if not self.target_group: return
        try:
            platform = self.context.get_platform(PlatformAdapterType.AIOCQHTTP)
            if platform:
                await platform.get_client().api.call_action('send_group_msg', group_id=int(self.target_group), message=text)
        except Exception as e:
            logger.error(f"消息发送失败: {e}")

    # --- 指令区域 ---

    @filter.command("start_server_monitor")
    async def cmd_start(self, event: AstrMessageEvent):
        if self.task and not self.task.done():
            yield event.plain_result("⚠️ 监控已在运行中")
        else:
            self.task = asyncio.create_task(self.monitor_task())
            yield event.plain_result(f"✅ 监控已启动 (间隔{self.check_interval}s)")

    @filter.command("stop_server_monitor")
    async def cmd_stop(self, event: AstrMessageEvent):
        if self.task:
            self.task.cancel()
            self.task = None
        yield event.plain_result("🛑 监控已停止")

    @filter.command("查询")
    async def cmd_query(self, event: AstrMessageEvent):
        data = await self._fetch_server_data()
        msg = self._format_msg(data)
        hito = await self.get_hitokoto()
        if hito: msg += f"\n\n💬 {hito}"
        yield event.plain_result(msg)

    @filter.command("reset_monitor")
    async def cmd_reset(self, event: AstrMessageEvent):
        self.last_player_count = None
        self.last_player_list = []
        yield event.plain_result("🔄 缓存已重置，下次检测将视为首次")

    @filter.command("set_group")
    async def cmd_setgroup(self, event: AstrMessageEvent, group_id: str):
        if group_id.isdigit():
            self.target_group = group_id
            yield event.plain_result(f"✅ 目标群已设为: {group_id}")
        else:
            yield event.plain_result("❌ 群号必须为纯数字")

    async def terminate(self):
        if self.task: self.task.cancel()
