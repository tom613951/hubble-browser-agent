import asyncio
import base64
import json
import logging
import re
import io
from PIL import Image
from playwright.async_api import async_playwright

logger = logging.getLogger("hubble.agent")

# JS Script to annotate DOM elements and assign data-hubble-id attributes
JS_ANNOTATION_SCRIPT = """
() => {
    // 1. Clean up any existing badges
    document.querySelectorAll('.hubble-badge').forEach(el => el.remove());
    
    const elements = [];
    // 2. Select interactive candidates
    const rawCandidates = document.querySelectorAll('button, a, input, select, textarea, [role="button"], [role="link"], [onclick]');
    const candidates = Array.from(rawCandidates);
    
    let visibleIndex = 1;
    
    candidates.forEach(el => {
        // Visibility and layout checking
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        
        if (rect.width < 3 || rect.height < 3 || 
            style.display === 'none' || style.visibility === 'hidden' || 
            style.opacity === '0') {
            return;
        }
        
        // Basic coordinates check to avoid offscreen elements cluttering the visual field
        if (rect.bottom < 0 || rect.top > window.innerHeight || 
            rect.right < 0 || rect.left > window.innerWidth) {
            return;
        }
        
        const tagName = el.tagName.toUpperCase();
        const type = el.getAttribute('type') || null;
        let text = el.innerText || '';
        
        // Fallbacks for empty inputs/buttons
        if (tagName === 'INPUT') {
            if (type === 'button' || type === 'submit') {
                text = el.value || '';
            } else {
                text = el.getAttribute('placeholder') || el.value || '';
            }
        }
        
        text = text.trim().replace(/\\s+/g, ' ');
        if (text.length > 50) text = text.substring(0, 50) + '...';
        
        const placeholder = el.getAttribute('placeholder') || '';
        const ariaLabel = el.getAttribute('aria-label') || '';
        const title = el.getAttribute('title') || '';
        
        // Create float badge
        const badge = document.createElement('div');
        badge.className = 'hubble-badge';
        badge.innerText = visibleIndex;
        
        const scrollY = window.scrollY || window.pageYOffset;
        const scrollX = window.scrollX || window.pageXOffset;
        
        badge.style.position = 'absolute';
        badge.style.top = `${rect.top + scrollY}px`;
        badge.style.left = `${rect.left + scrollX}px`;
        badge.style.backgroundColor = '#ff007f'; // Cyber pink
        badge.style.color = '#ffffff';
        badge.style.fontSize = '9px';
        badge.style.fontWeight = 'bold';
        badge.style.fontFamily = 'monospace';
        badge.style.padding = '1px 3px';
        badge.style.borderRadius = '3px';
        badge.style.border = '1px solid #ffffff';
        badge.style.zIndex = '10000000';
        badge.style.pointerEvents = 'none';
        badge.style.boxShadow = '0 1px 3px rgba(0,0,0,0.4)';
        
        document.body.appendChild(badge);
        
        // Set tracker attribute
        el.setAttribute('data-hubble-id', visibleIndex);
        
        elements.push({
            id: visibleIndex,
            tag: tagName,
            type: type,
            text: text || null,
            placeholder: placeholder || null,
            aria_label: ariaLabel || null,
            title: title || null
        });
        
        visibleIndex++;
    });
    
    return elements;
};
"""

JS_CLEANUP_SCRIPT = """
() => {
    document.querySelectorAll('.hubble-badge').forEach(el => el.remove());
}
"""

SYSTEM_PROMPT = """你是一个高能力的网页自动化智能体 (AI Browser Agent)。你的任务是操作网页浏览器来达成用户的目标。
你会接收到：
1. 用户的最终目标 (Goal)
2. 当前页面的 URL 和标题 (Title)
3. 页面上所有可见的交互元素的列表。列表中的每个元素都有一个唯一的数字 ID。
4. 当前页面的屏幕截图。屏幕截图中，每个交互元素都被标记了一个粉红色的数字标签，其编号与元素列表的 ID 一一对应。

你的职责是根据目标和当前的网页状态，决定执行哪一步操作。

请返回一个符合以下 JSON 格式的响应，不要输出多余的文本：
{
  "thought": "你的分析、思考过程以及下一步的计划。注意中文输出。",
  "action": {
    "type": "click" | "type" | "select" | "scroll" | "navigate" | "go_back" | "wait" | "finish",
    "id": 选填 (当类型为 click, type, select 时必填，指定对应的元素 ID),
    "text": 选填 (当类型为 type 时必填，指定要输入的文本),
    "value": 选填 (当类型为 select 时必填，指定下拉选择的 value),
    "url": 选填 (当类型为 navigate 时必填，指定目标 URL),
    "direction": 选填 (当类型为 scroll 时必填，可选 'up' 或 'down'),
    "seconds": 选填 (当类型为 wait 时必填，等待时间，例如 2),
    "answer": 选填 (当类型为 finish 时必填，提供给用户的最终详尽解答或抓取的数据)
  }
}

注意规则：
- 仔细核对网页截图中的粉红数字标签，确保操作的 ID 在当前元素列表中。
- 输入文本时，确保目标元素是 INPUT 或 TEXTAREA。
- 如果已经找到了信息或完成了目标，请立刻执行 "finish" 动作，并在 "answer" 中总结您的输出。
- 如果需要向下浏览页面寻找元素，使用 "scroll" 动作，方向为 "down"。
"""

def parse_json_response(text):
    # Regex to extract JSON block from markdown code blocks
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if match:
        json_str = match.group(1).strip()
    else:
        json_str = text.strip()
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # Fallback to look for raw outer braces
        start = json_str.find('{')
        end = json_str.rfind('}')
        if start != -1 and end != -1:
            try:
                return json.loads(json_str[start:end+1])
            except json.JSONDecodeError:
                pass
        raise ValueError(f"未能将大模型响应解析为有效的 JSON。原始输出:\n{text}")

class HubbleAgent:
    def __init__(self, provider, api_key, model_name, goal, start_url=None, max_steps=15, headless=True, on_log=None, on_state=None):
        self.provider = provider
        self.api_key = api_key
        self.model_name = model_name
        self.goal = goal
        self.start_url = start_url
        self.max_steps = max_steps
        self.headless = headless
        
        # Callbacks
        self.on_log = on_log or (lambda msg, lvl: None)
        self.on_state = on_state or (lambda state: None)
        
        # Playwright resources
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def log(self, message, level="system"):
        logger.info(f"[{level}] {message}")
        if asyncio.iscoroutinefunction(self.on_log):
            await self.on_log(message, level)
        else:
            self.on_log(message, level)

    async def send_state(self, step, screenshot_bytes, thought, url, thinking=True):
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode('utf-8') if screenshot_bytes else None
        state = {
            "step": step,
            "max_steps": self.max_steps,
            "url": url,
            "screenshot": screenshot_b64,
            "thought": thought,
            "thinking": thinking
        }
        if asyncio.iscoroutinefunction(self.on_state):
            await self.on_state(state)
        else:
            self.on_state(state)

    async def run(self):
        try:
            # 1. Initialize Browser
            await self.log("正在启动 Playwright 浏览器...", "system")
            self.playwright = await async_playwright().start()
            
            # Launch Chromium
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=["--disable-web-security", "--no-sandbox"]
            )
            
            # Setup Context (emulating desktop)
            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            self.page = await self.context.new_page()
            
            # 2. Open initial page
            current_url = self.start_url or "https://www.baidu.com"
            await self.log(f"正在导航至初始网页: {current_url}", "system")
            try:
                await self.page.goto(current_url, wait_until="domcontentloaded", timeout=30000)
                await self.page.wait_for_timeout(2000) # Give extra time for visual load
            except Exception as e:
                await self.log(f"无法访问初始网页 {current_url}: {str(e)}。请检查网络或代理/VPN设置，智能体将尝试在空白页继续运行。", "error")

            step = 1
            while step <= self.max_steps:
                await self.log(f"--- Step {step} / {self.max_steps} ---", "system")
                
                # Retrieve URL and title
                url = self.page.url
                title = await self.page.title()
                
                # 3. Annotate page
                await self.log("正在扫描并标注页面元素...", "system")
                try:
                    elements = await self.page.evaluate(JS_ANNOTATION_SCRIPT)
                except Exception as e:
                    await self.log(f"DOM标注失败: {str(e)}，正在重试直接截屏...", "system")
                    elements = []

                # Capture annotated screenshot
                screenshot_bytes = await self.page.screenshot(type="png")
                
                # Cleanup badges immediately to restore raw web state
                await self.page.evaluate(JS_CLEANUP_SCRIPT)
                
                # Notify frontend about current screenshot and thought transition
                await self.send_state(step, screenshot_bytes, f"正在思考下一步行动... (正在请求 LLM {self.model_name})", url, thinking=True)
                
                # Assemble text context for LLM
                element_desc_list = []
                for el in elements:
                    desc = f"ID: {el['id']} | Tag: {el['tag']}"
                    if el['type']: desc += f" | Type: {el['type']}"
                    if el['text']: desc += f" | Text: \"{el['text']}\""
                    if el['placeholder']: desc += f" | Placeholder: \"{el['placeholder']}\""
                    if el['aria_label']: desc += f" | Aria-Label: \"{el['aria_label']}\""
                    element_desc_list.append(desc)
                
                elements_text = "\n".join(element_desc_list)
                
                prompt_content = f"""用户目标：{self.goal}
当前页面标题：{title}
当前页面 URL：{url}

页面交互元素列表：
{elements_text}
"""
                # 4. Get reasoning & action from LLM
                await self.log(f"正在向大模型 ({self.model_name}) 发送页面状态分析请求...", "agent")
                try:
                    action_json = await self._call_llm(prompt_content, screenshot_bytes)
                except Exception as e:
                    await self.log(f"大模型调用失败: {str(e)}", "error")
                    raise e
                
                thought = action_json.get("thought", "无思考描述")
                action_data = action_json.get("action", {})
                action_type = action_data.get("type", "").lower()
                
                await self.log(f"思考过程: {thought}", "agent")
                
                # Check for finish action
                if action_type == "finish":
                    answer = action_data.get("answer", "任务已完成，但未提供具体答案。")
                    await self.log(f"完成任务！答案已返回。", "success")
                    await self.send_state(step, screenshot_bytes, thought, url, thinking=False)
                    return answer
                
                # 5. Execute Action
                await self._execute_action(action_data, elements)
                
                # Render clean (no badge) screenshot for the step history on frontend
                clean_screenshot = await self.page.screenshot(type="png")
                await self.send_state(step, clean_screenshot, thought, url, thinking=False)
                
                step += 1
                await asyncio.sleep(2)  # Pause to let the page load animations or network settle
            
            raise TimeoutError(f"已达到最大步数限制 ({self.max_steps})，但任务未完成。")
            
        finally:
            # 6. Cleanup resources
            await self.log("正在关闭浏览器...", "system")
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()

    async def _call_llm(self, text_context, screenshot_bytes):
        if self.provider == "gemini":
            from google import genai
            
            # Setup Gemini client using new google-genai unified SDK
            client = genai.Client(api_key=self.api_key)
            try:
                # Load screenshot image
                img = Image.open(io.BytesIO(screenshot_bytes))
                
                full_prompt = f"{SYSTEM_PROMPT}\n\n=======================\n\n{text_context}"
                response = await client.aio.models.generate_content(
                    model=self.model_name,
                    contents=[img, full_prompt]
                )
                return parse_json_response(response.text)
            finally:
                await client.aio.aclose()
            
        elif self.provider in ("openai", "ollama"):
            from openai import AsyncOpenAI
            
            # Base URL configuration
            if self.provider == "ollama":
                client = AsyncOpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
            else:
                client = AsyncOpenAI(api_key=self.api_key)
                
            # Base64 encode screenshot
            img_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            # Call chat completions
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": text_context},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{img_b64}"
                    }}
                ]}
            ]
            
            # Force JSON mode for OpenAI
            response = await client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                response_format={"type": "json_object"}
            )
            
            return parse_json_response(response.choices[0].message.content)
            
        elif self.provider == "deepseek":
            from openai import AsyncOpenAI
            
            # DeepSeek has an OpenAI-compatible endpoint
            client = AsyncOpenAI(base_url="https://api.deepseek.com", api_key=self.api_key)
            
            # DeepSeek-V3/R1 is text-only, so we strip visual guidelines from the system prompt
            text_prompt = SYSTEM_PROMPT.replace("4. 当前页面的屏幕截图。屏幕截图中，每个交互元素都被标记了一个粉红色的数字标签，其编号与元素列表的 ID 一一对应。", "")
            text_prompt = text_prompt.replace("- 仔细核对网页截图中的粉红数字标签，确保操作的 ID 在当前元素列表中。", "- 仔细核对网页交互元素列表，确保操作的 ID 在列表中。")
            
            messages = [
                {"role": "system", "content": text_prompt},
                {"role": "user", "content": text_context}
            ]
            
            response = await client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                response_format={"type": "json_object"}
            )
            
            return parse_json_response(response.choices[0].message.content)
            
        elif self.provider == "anthropic":
            import anthropic
            
            client = anthropic.AsyncAnthropic(api_key=self.api_key)
            img_b64 = base64.b64encode(screenshot_bytes).decode('utf-8')
            
            response = await client.messages.create(
                model=self.model_name,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": img_b64
                                }
                            },
                            {
                                "type": "text",
                                "text": text_context
                            }
                        ]
                    }
                ]
            )
            
            return parse_json_response(response.content[0].text)
            
        else:
            raise ValueError(f"不支持的 LLM 供应商: {self.provider}")

    async def _execute_action(self, action, elements):
        action_type = action.get("type", "").lower()
        
        if action_type == "navigate":
            url = action.get("url")
            if not url:
                raise ValueError("操作 'navigate' 必须提供 'url' 参数。")
            await self.log(f"正在导航至: {url}", "action")
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                await self.log(f"导航至 {url} 失败: {str(e)}。请检查网络或代理/VPN设置。", "error")
            
        elif action_type == "go_back":
            await self.log("正在返回上一页...", "action")
            await self.page.go_back(wait_until="domcontentloaded")
            
        elif action_type == "scroll":
            direction = action.get("direction", "down").lower()
            await self.log(f"正在向 {direction} 滚动页面...", "action")
            if direction == "up":
                await self.page.evaluate("window.scrollBy(0, -window.innerHeight * 0.8)")
            else:
                await self.page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
                
        elif action_type == "wait":
            seconds = int(action.get("seconds", 2))
            await self.log(f"等待 {seconds} 秒...", "action")
            await asyncio.sleep(seconds)
            
        elif action_type in ("click", "type", "select"):
            el_id = action.get("id")
            if el_id is None:
                raise ValueError(f"操作 '{action_type}' 必须提供元素 'id' 参数。")
            
            # Find the selector
            selector = f"[data-hubble-id='{el_id}']"
            
            # Confirm element exists in our elements list
            element_meta = next((e for e in elements if e["id"] == int(el_id)), None)
            if not element_meta:
                raise ValueError(f"元素 ID {el_id} 不在当前页面的可用元素列表中。")
            
            # Highlight and verify in Playwright DOM before interacting
            try:
                locator = self.page.locator(selector)
                await locator.scroll_into_view_if_needed()

                if action_type == "click":
                    await self.log(f"正在点击元素 {el_id} ({element_meta['tag']} - {element_meta['text'] or ''})", "action")
                    await locator.click(timeout=10000)
                    
                elif action_type == "type":
                    text = action.get("text")
                    if text is None:
                        raise ValueError("操作 'type' 必须提供 'text' 参数。")
                    await self.log(f"正在往元素 {el_id} 输入内容: \"{text}\"", "action")
                    # Click, select all, delete, then fill to ensure input is clean
                    await locator.click(timeout=5000)
                    await self.page.keyboard.press("Control+A")
                    await self.page.keyboard.press("Backspace")
                    await locator.fill(text, timeout=5000)
                    
                elif action_type == "select":
                    value = action.get("value")
                    if value is None:
                        raise ValueError("操作 'select' 必须提供 'value' 参数。")
                    await self.log(f"正在将选择框元素 {el_id} 值设为: \"{value}\"", "action")
                    await locator.select_option(value=value, timeout=5000)
            except Exception as e:
                await self.log(f"操作元素 {el_id} ({action_type}) 失败: {str(e)}", "error")
        else:
            raise ValueError(f"未知或不支持的操作类型: {action_type}")
