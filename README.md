# Nifty-Hubble: Visual AI Web Automation Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Playwright](https://img.shields.io/badge/Playwright-Supported-green.svg)](https://playwright.dev/)

**Nifty-Hubble** 是一个基于 **Python + Playwright + FastAPI** 开发的**可视化智能网页自动化智能体 (Visual AI Browser Agent)**。它能够解析自然语言任务目标，自主决定并执行浏览器动作（包括导航、滚动、输入、点击、选择、等待等），最终抽取您所需要的数据或总结任务结果。

项目配备了一个**极具科技感、毛玻璃暗黑主题 (Glassmorphism)** 的前端交互式 Web UI 控制台。您可以通过它输入指令，实时查看智能体每一步的思考链 (Thought)、执行的操作 (Action)，以及浏览器视图在操作过程中的实时画面更新。

---

## 🌟 项目亮点

1. **视觉交互感知 (Visual Grounding)**：
   - 智能体采用主流的 DOM 视觉标记方案。在思考前，引擎会自动为当前视图内的所有可见交互元素（按钮、链接、输入框、下拉框）打上临时的粉红色数字标签。
   - 大模型（支持 Gemini 与 OpenAI）结合**视觉截图**与**标记元素列表**，能精准点对点地执行点击和输入指令，规避了传统 CSS 选择器经常失效或多级嵌套导致的解析不准问题。

2. **极具科技感的毛玻璃前端控制台**：
   - 完全响应式的 Glassmorphism 布局设计。
   - 实时双向通信：基于 WebSocket，秒级更新智能体心智日志（Thought、Action、Observation）与浏览器无头窗口当前画面截图。
   - 提供可视化操作选项：允许选择 LLM 供应商、动态填入 API 密钥、调节最大执行步数限制、开启/关闭无头渲染等。

3. **开箱即用的多模型兼容**：
   - **Google Gemini**：首选使用 `gemini-3.5-flash` 多模态模型，运行效率高，成本极低。
   - **OpenAI**：兼容 `gpt-5.5-mini` 等多模态大语言模型。
   - **DeepSeek**：兼容 `deepseek-v4-flash` / `deepseek-v4-pro`。
   - **Anthropic Claude**：兼容 `claude-4.8` 等多模态大语言模型。

---

## 📁 目录结构

```text
├── backend/
│   ├── main.py            # FastAPI WebSocket 服务器 (静态资源托管与通信逻辑)
│   ├── agent.py           # 智能体核心逻辑 (Playwright 驱动、DOM 标注器、LLM 思考循环)
│   └── requirements.txt   # Python 依赖依赖库声明
│
├── frontend/
│   ├── index.html         # 科技感 Web 控制台主页
│   ├── style.css          # Glassmorphism/Dark 样式文件
│   └── app.js             # WebSocket 客户端状态管理与渲染逻辑
│
├── README.md              # 本说明文档
└── LICENSE                # MIT 开源协议
```

---

## 🚀 快速开始

### 第一步：克隆仓库并安装依赖

在项目根目录下打开终端，安装项目所需的 Python 库依赖：

```bash
pip install -r backend/requirements.txt
```

接下来，安装 **Playwright** 所需的浏览器内核驱动（该步骤会自动下载并配置 Chromium）：

```bash
playwright install
```

### 第二步：配置 API 密钥 (可选)

您可以在本地创建一个 `.env` 文件，或直接将您的 API 密钥配置进系统环境变量，这样在使用网页控制台时无需重复输入密钥：

```env
# 若使用 Google Gemini (推荐)
GEMINI_API_KEY=your_gemini_api_key_here

# 若使用 OpenAI
OPENAI_API_KEY=your_openai_api_key_here
```

*注意：如果您不在环境变量中配置，也可以直接在运行网页后的前端界面输入框中手动填写 API Key。*

### 第三步：运行后端服务

在项目根目录下运行 FastAPI 服务：

```bash
python -m uvicorn backend.main:app
```

> **💡 Windows 环境提示：** 
> 在 Windows 系统上，Playwright 要求 asyncio 使用 `ProactorEventLoop` 才能正常唤起浏览器子进程。然而，Uvicorn 在开启 `--reload` 自动重载模式下，会强行将事件循环切换为不支持子进程的 `SelectorEventLoop`，从而引发 `NotImplementedError` 错误。
>
> 我们已在 `backend/main.py` 的首行加入了显式的事件循环策略修复代码。但为了保持最佳的运行稳定性，在 Windows 上建议**不要加 `--reload` 参数**启动服务。

终端将输出服务运行地址，例如：
```text
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

### 第四步：打开控制台进行操作

1. 用浏览器打开 `http://127.0.0.1:8000`。
2. **选择 LLM 供应商** 并配置好模型名称（如 `gemini-3.5-flash`、`gpt-5.5-mini`、`deepseek-v4-flash`、`claude-4.8` 等）。
3. **输入目标网址** (如 `https://wikipedia.org` 或留空) 与 **任务目标**。
   - *示例目标：在维基百科搜索 "Artificial intelligence"，点击第一个链接，并在正文中找出是由谁在何年提出该术语的。*
4. 点击 **“启动智能体”**。
5. 控制台将动态弹出进度，您能看见智能体屏幕在实时增加红粉色交互标签，并进行点击、键盘输入，右侧控制台会滚屏展示智能体每步推理的 Thought 以及动作，最终结果将渲染在“最终任务结果”卡片中。

---

## 🛠️ 技术原理简介

1. **DOM 节点遍历与过滤**：在每次决策前，后端会在当前页面执行定制的 Javascript 脚本，提取可视、尺寸大于零、处于当前视口内的 `a, button, input, select, textarea` 等强交互元素。
2. **视觉绑定 (Visual Badging)**：给每个交互元素生成对应 ID 的粉色角标贴在元素边缘，接着给浏览器当前有角标的页面拍摄一张 PNG 截图。
3. **闭环决策控制 (ReAct Loop)**：
   - 大模型读取该带角标的截图以及元素文本元数据。
   - 大模型进行推理，并生成结构化的 JSON 指令（如 `{"action": {"type": "click", "id": 5}}`）。
   - 后端清除角标，使用 Playwright 的 Selector 进行实际触发。
   - 循环执行，直到模型发出 `finish` 指令或超过最大设定步数。

---

## 📄 开源许可证

本项目采用 [MIT License](LICENSE) 开源许可协议。
