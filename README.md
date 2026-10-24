# 海康 NVR 离家布防 · Home Assistant 集成

一个完全本地运行的 Home Assistant 自定义集成。它创建一个开关，通过海康
ISAPI 控制指定通道的：

> 周界防范 → 区域入侵检测 → 联动方式 → **上传中心**

开关打开时，为所有已选通道添加“上传中心 / Notify Surveillance Center”联动；
关闭时，只移除这一项联动，**不会改动**录像、声音、白光、报警输出等其他设置。
开关状态会从 NVR 的实际配置读取，并在 NVR 网页端被手动修改后自动同步。

集成本身不依赖云服务，也不需要 Home Assistant 的 HACS 才能运行。

## 使用场景

家里的摄像头需要**持续开启侦测和录像**，方便事后调取监控；但不希望每次有人
经过都收到手机推送。因此只在“离家”时打开 NVR 的“上传中心”联动，让手机收到
告警通知；回家后关闭上传，摄像头仍然正常侦测和录像。

典型的 Home Assistant 自动化如下（把实体和人员替换成实际值）：

```yaml
alias: 离家时开启摄像头上报
triggers:
  - trigger: zone
    entity_id: person.someone
    zone: zone.home
    event: leave
actions:
  - action: switch.turn_on
    target:
      entity_id: switch.li_jia_bu_fang_mo_shi

---
alias: 到家后关闭摄像头上报
triggers:
  - trigger: zone
    entity_id: person.someone
    zone: zone.home
    event: enter
actions:
  - action: switch.turn_off
    target:
      entity_id: switch.li_jia_bu_fang_mo_shi
```

也可以直接在 Apple 家庭中创建“第一个人离开家 / 最后一个人到家”的自动化来
控制这个开关。

> 注意：“上传中心”是 NVR 侧的海康推送联动（通常对应 Hik-Connect / iVMS 手机
> 通知）。手机能否收到推送，还取决于 NVR 是否已绑定 Hik-Connect 以及海康 App
> 的通知权限。Home Assistant 本身不会把 NVR 事件自动推送到手机；如果希望通过
> Home Assistant 发通知，需要另外编写自动化/通知动作。

## 功能

- 一个开关同时控制一个或多个 NVR 通道（例如 7 和 8 通道）。
- 只有所有已选通道的上传中心都打开时，开关才显示为“开”；任一通道关闭时显示“关”。
- 打开/关闭会以幂等方式写入 NVR，重复操作不会产生多余修改。
- 写入时保留同一事件的所有其他联动方式（录像、音频、白光等）。
- 轮询间隔可配置（默认 30 秒，范围 10–300 秒）。
- 支持 HTTP Digest 认证（也兼容 Basic），支持 HTTPS 和自签名证书。
- 自动创建 Home Assistant 设备，包含 NVR 型号、固件、序列号等信息。
- 支持 Home Assistant 的 HomeKit Bridge，从而接入 Apple 家庭 App。
- 支持中英文界面、重新认证（reauth）、选项修改和 diagnostics 下载。

## 已验证环境

- NVR：Hikvision 8 通道 NVR（V4.85 系列固件）
- NVR 通道：多个 IP 通道
- Home Assistant：2025.1.4（自动化测试）以及 2026.8.3（实际部署）
- ISAPI：`/ISAPI/Event/triggers/fielddetection-<n>`，
  `notificationMethod=center`

不同型号/固件的海康设备可能使用不同的智能事件路径。如果所选通道返回
`channel_not_supported`，请先确认 NVR 网页端确实有“区域入侵检测”配置。

## 工作原理

海康 NVR 将“周界防范 → 区域入侵检测”暴露为 `fielddetection` 事件，
每个通道对应一个事件触发器：

```text
GET /ISAPI/Event/triggers/fielddetection-7
```

其中“上传中心”是 `EventTriggerNotificationList` 里的一个通知方式：

```xml
<EventTriggerNotification>
  <id>center</id>
  <notificationMethod>center</notificationMethod>
</EventTriggerNotification>
```

集成读取该 XML，判断 `center` 是否存在，并在写入时只增删这一个 XML 块，
其余内容原样保留。所有 HTTP 请求都在 Home Assistant 的 executor 线程中执行，
并用锁串行化，避免并发写入。

## 安装

### 方式 A：HACS（推荐）

1. 在 HACS → 集成 → 右上角菜单 → **自定义存储库** 中添加
   `https://github.com/wpcwzy/hikvision-away-ha`，类别选择“Integration”。
2. 在 HACS 中搜索 **Hikvision NVR Away Arm**，安装。
3. 重启 Home Assistant。

### 方式 B：手动复制

将本仓库的 `custom_components/hikvision_away` 整个目录复制到 Home Assistant
配置目录下的 `custom_components/` 中，即：

```text
/config/custom_components/hikvision_away/
```

然后重启 Home Assistant。

重启后在“设置 → 设备与服务 → 添加集成”中搜索
**Hikvision NVR Away Arm**。

## 配置

添加集成时需要填写：

| 字段 | 示例 | 说明 |
| --- | --- | --- |
| NVR 地址 | `192.168.1.100` | NVR 的 IP 或主机名，也可以直接粘贴 `http://ip:port` |
| 端口 | `80` | 网页/ISAPI 端口；HTTPS 通常为 443 |
| 使用 HTTPS | 关 | 如果 NVR 只开放 HTTPS 则打开 |
| 校验 TLS 证书 | 关 | 海康自签名证书一般保持关闭 |
| 用户名 | `admin` | 建议使用单独的、具备远程配置权限的账号 |
| 密码 | — | 仅保存在 Home Assistant 配置中 |
| 通道 | `7 - 餐厅`、`8 - 客厅` | 可多选，由一个开关统一控制 |
| 开关名称 | `离家布防模式` | 显示名称和实体名称 |

> 如果 NVR 上存在 7、8 通道，配置页面会默认预选这两个通道；你可以按需调整。
> 一个配置条目 = 一个开关 = 一组通道。若要为不同通道组分别创建开关，
> 再次添加集成即可。

## 实体

- 实体：`switch.*`（实际 entity_id 由 Home Assistant 根据设备名和开关名称生成；
  新版 Home Assistant 可能包含设备名前缀，请在“开发者工具 → 状态”中查看，
  或直接从集成页面打开实体）
- 打开：为所有已选通道开启“上传中心”
- 关闭：为所有已选通道关闭“上传中心”
- 图标：`mdi:shield-home` / `mdi:shield-home-outline`

常用属性：

| 属性 | 说明 |
| --- | --- |
| `channels` | 受控通道列表，例如 `[7, 8]` |
| `channel_names` | 通道名称，例如 `["餐厅", "客厅"]` |
| `channel_states` | 每个通道的实际状态，例如 `{"7": true, "8": false}` |
| `notification_methods` | 每个通道当前的 ISAPI 联动方式 |
| `isapi_endpoints` | 对应的事件触发器路径 |
| `nvr_host` / `nvr_port` | NVR 地址和端口 |

如果只有一个通道处于开启状态，开关会显示“关”，`channel_states` 会显示
具体是哪个通道未开启。再次打开开关会把所有通道都打开。

## 接入 Apple 家庭（HomeKit）

不需要编写 HomeKit 代码，使用 Home Assistant 内置的 **HomeKit Bridge** 即可把
这个 switch 暴露给 Apple 家庭 App。

### UI 配置（推荐）

1. 设置 → 设备与服务 → 添加集成 → **HomeKit Bridge**。
2. 选择要暴露的域/实体：
   - 简单方式：包含 `switch` 域；
   - 精确方式：包含实体 `switch.xxx`（以“开发者工具 → 状态”中的实际 entity_id 为准）。
3. 完成创建后，Home Assistant 会显示二维码和 8 位配对码。
4. 打开 iPhone/iPad 的“家庭”App → `+` → 添加配件 → 扫描二维码 →
   输入配对码。
5. 在“家庭”中把开关命名为“离家布防”（如果显示的默认名称较长，可以直接在
   Apple 家庭 App 中重命名，不影响集成）。

如果已经存在 HomeKit Bridge，可直接在它的“配置”里把新实体加入包含列表，
然后重新加载 HomeKit 集成。HomeKit Bridge 的配置修改后通常需要几秒到一分钟，
Apple 家庭才会刷新出新的配件。

YAML 示例见 [homekit_configuration_example.yaml](homekit_configuration_example.yaml)。
如果已经通过 UI 创建了 Bridge，请不要重复添加同名 YAML 配置。

## 验证

1. 打开 Home Assistant 开发者工具 → 状态，找到开关，确认初始状态与 NVR 一致。
2. 打开开关；等待 1–2 秒后刷新 NVR 网页端：
   通道管理 → 智能分析 → 周界防范 → 区域入侵检测 → 联动方式，
   确认“上传中心”已勾选。
3. 关闭开关，确认“上传中心”取消，而录像等其他联动仍然保留。
4. 在 Apple 家庭中操作开关，观察 NVR 网页端状态同步变化。

也可以不经过 Home Assistant 直接用仓库里的脚本做只读检查：

```bash
export HIKVISION_HOST=192.168.1.100
export HIKVISION_USERNAME=admin
export HIKVISION_PASSWORD='...'
python scripts/smoke_test_live.py --channel 7
```

写入测试（会短暂打开再关闭上传中心，并恢复原状态）：

```bash
python scripts/smoke_test_live.py --channel 8 --toggle --yes
```

## 故障排查

| 现象 | 处理 |
| --- | --- |
| `invalid_auth` | 确认用户名/密码；海康默认使用 HTTP Digest；确认账号未被锁定 |
| `cannot_connect` | 确认 IP、端口、HTTP/HTTPS、Home Assistant 与 NVR 是否在同一网络 |
| `api_error` | NVR 拒绝了 ISAPI 请求；确认已启用 ISAPI、账号有远程配置权限 |
| `channel_not_supported` | 该通道没有 `fielddetection` 事件；确认固件/型号支持区域入侵检测 |
| 开关不可用 | NVR 离线或认证失败；集成会按轮询间隔自动重试 |
| 开关状态不更新 | 调小轮询间隔；检查 NVR 是否限制了并发登录数 |
| HomeKit 中看不到开关 | 检查 HomeKit Bridge 的包含实体列表，重新加载 HomeKit 集成 |

调试日志：

```yaml
logger:
  logs:
    custom_components.hikvision_away: debug
```

集成设置页可以下载 diagnostics，密码会被自动脱敏。

## 已知限制

- 至少需要 NVR 的 ISAPI 远程配置权限。
- 一个配置条目只创建一个开关，但一个开关可以控制多个通道。
- 写入操作会在 NVR 上真实修改“联动方式”，请确认通道正确后再操作。
- 不同海康固件的 `fielddetection` 路径可能不同；欢迎反馈设备型号和固件版本。
- 本集成只控制“上传中心”，不会开启/关闭整个区域入侵检测功能本身。
- 集成不会修改 NVR 的用户、网络或其他配置。

## 安全说明

- 所有通信都在本地网络完成，不经过云端。
- 凭据保存在 Home Assistant 的配置存储中，不会写入日志或 diagnostics。
- 建议为 Home Assistant 创建一个单独的海康账号，只授予必要的远程配置权限。
- 如果使用 HTTPS 且关闭证书校验，请确认 NVR 位于可信局域网。

## 开发与测试

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest -q
```

测试包括：

- XML 增删“上传中心”的纯函数测试；
- Digest/Basic 认证、错误响应、通道解析等 HTTP 客户端测试；
- 基于 `pytest-homeassistant-custom-component` 的配置流、选项流、实体开关和
  diagnostics 测试。

## 许可

MIT License，详见 [LICENSE](LICENSE)。
