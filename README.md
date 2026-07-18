# Windows 截图工具 — 豆包 AI 解释版

一个 Windows 截图工具，截图后自动调用豆包（火山引擎 Ark）视觉模型进行 AI 分析，结果以弹窗显示。

## 功能

- **F9 全局快捷键截图**：后台待机，随时按 F9 框选屏幕区域
- **拖拽框选**：半透明遮罩 + 拖拽选区，体验类似系统截图工具
- **AI 视觉分析**：截图自动发送到豆包 Seed 推理模型，流式返回详细描述
- **弹窗结果**：分析结果在独立窗口中流式显示，可复制、可关闭
- **零额外依赖**：全局快捷键用 ctypes 直接调 Windows API，无需 pynput/keyboard 等第三方库

## 效果截图

<img width="692" height="533" alt="image" src="https://github.com/user-attachments/assets/cb63f1be-aba1-4ed4-b9a6-ed808b2ecea2" />




## 环境要求

- Windows 10/11
- Python 3.10+
- 火山引擎 Ark 账号 + 已创建的推理端点

## 安装

```powershell
pip install -r requirements.txt
```

## 配置

在 `API KEY.txt` 中配置以下三行（已包含你的配置）：

```
DeepSeek API：sk-xxx
DouBao API：ark-xxx
豆包推理接入点：ep-20260718110702-zdjp2
```

推理端点需在 [火山引擎 Ark 控制台](https://console.volcengine.com/ark/) 创建，选择视觉模型（推荐 `doubao-1.5-vision-pro-250328`）。

## 使用

### 后台模式（推荐）

```powershell
python screenshot_tool.py
```

启动后终端显示提示信息，工具进入后台待机。在任何窗口按 **F9** 即可截图：

1. 按 F9 → 全屏半透明遮罩弹出
2. 拖拽鼠标框选区域（或按 Enter 截全屏、Esc 取消）
3. 松手后自动截图并调用豆包 API
4. 弹窗流式显示分析结果
5. 关闭弹窗，回到待机状态

### 单次模式

```powershell
python screenshot_tool.py --once
```

截图一次后自动退出，结果输出到终端。

### 自定义快捷键

```powershell
python screenshot_tool.py -k Ctrl+Shift+F9
```

### 自定义提示词

```powershell
python screenshot_tool.py -p "这张截图里的代码在做什么？"
```

### 保存截图

```powershell
python screenshot_tool.py -o my_screenshot.jpg
```

## 项目结构

```
截图工具/
├── screenshot_tool.py   # 主程序
├── API KEY.txt          # API Key 和端点 ID 配置
└── requirements.txt     # Python 依赖
```

## 依赖

| 库 | 用途 |
|---|---|
| `mss` | 跨平台截图 |
| `Pillow` | 图片压缩处理 |
| `requests` | HTTP 调用豆包 API |
| `tkinter` | 内置，遮罩和弹窗 UI |
| `ctypes` | 内置，Windows 全局快捷键 |

## 技术要点

- 单 Tkinter 根窗口 + Toplevel 子窗口架构，避免嵌套事件循环冲突
- 流式 API 调用：豆包 Seed 推理模型思考时间长，非流式易超时
- 截图自动压缩：等比缩放到 1920px 以内，JPEG 85% 质量，控制传输体积
- DPI 缩放兼容：截图坐标直接来自 Tkinter 事件，与 mss 像素坐标一一对应
- Windows 全局快捷键通过 `RegisterHotKey` + `PeekMessage` 实现，后台线程监听
