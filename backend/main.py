import sys
import asyncio

if sys.platform == 'win32':
    # Playwright requires ProactorEventLoop on Windows to support subprocesses.
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import os
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from backend.agent import HubbleAgent

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("hubble.main")

# Load environment variables
load_dotenv()

app = FastAPI(title="Nifty-Hubble API")

# Setup WebSocket endpoint for running the agent
@app.websocket("/ws/run")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connection established.")
    
    # Helper to send log messages to client
    async def send_client_log(message, level="system"):
        try:
            await websocket.send_json({
                "type": "log",
                "message": message,
                "level": level
            })
        except Exception:
            pass

    # Helper to send agent state updates to client
    async def send_client_state(state_dict):
        try:
            await websocket.send_json({
                "type": "state",
                **state_dict
            })
        except Exception:
            pass

    try:
        # 1. Receive initial configuration
        config_text = await websocket.receive_text()
        config = json.loads(config_text)
        logger.info(f"Received config: {config}")
        
        provider = config.get("provider")
        api_key = config.get("api_key")
        model_name = config.get("model")
        goal = config.get("goal")
        start_url = config.get("url")
        max_steps = config.get("max_steps", 15)
        headless = config.get("headless", True)
        
        if not goal:
            await websocket.send_json({"type": "error", "message": "Missing goal field."})
            await websocket.close()
            return
            
        # 2. Extract API Key if missing (falling back to environment variables)
        if not api_key:
            if provider == "gemini":
                api_key = os.getenv("GEMINI_API_KEY")
                if not api_key:
                    # Support general GOOGLE_API_KEY as well
                    api_key = os.getenv("GOOGLE_API_KEY")
            elif provider == "openai":
                api_key = os.getenv("OPENAI_API_KEY")
            elif provider == "deepseek":
                api_key = os.getenv("DEEPSEEK_API_KEY")
            elif provider == "anthropic":
                api_key = os.getenv("ANTHROPIC_API_KEY")
            
            # API key checking for commercial models
            if provider != "ollama" and not api_key:
                raise ValueError(f"未提供 API 密钥，且在系统环境变量中未检测到对应的 {provider.upper()} 密钥。请在配置栏中输入或检查本地环境变量配置。")
        
        # 3. Instantiate Agent
        agent = HubbleAgent(
            provider=provider,
            api_key=api_key,
            model_name=model_name,
            goal=goal,
            start_url=start_url,
            max_steps=max_steps,
            headless=headless,
            on_log=send_client_log,
            on_state=send_client_state
        )
        
        # 4. Define background execution task
        agent_run_task = asyncio.create_task(agent.run())
        
        # 5. Define WebSocket message listener (for stop signals or disconnects)
        async def listen_to_ws_control():
            try:
                while True:
                    control_text = await websocket.receive_text()
                    control_msg = json.loads(control_text)
                    if control_msg.get("control") == "stop":
                        await send_client_log("收到前端中止指令，正在中止智能体...", "system")
                        agent_run_task.cancel()
                        break
            except WebSocketDisconnect:
                logger.info("WebSocket disconnected by client.")
                agent_run_task.cancel()
            except Exception as e:
                logger.error(f"WebSocket listener error: {str(e)}")
                agent_run_task.cancel()

        ws_listener_task = asyncio.create_task(listen_to_ws_control())
        
        try:
            # Await the agent completion
            result = await agent_run_task
            # Notify final success
            await websocket.send_json({
                "type": "result",
                "result": result
            })
        except asyncio.CancelledError:
            logger.info("Agent run task was cancelled.")
            await websocket.send_json({
                "type": "error",
                "message": "任务已被用户或系统终止。"
            })
        except Exception as e:
            logger.exception("Error in agent execution loop.")
            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })
        finally:
            # Cleanup listener task
            ws_listener_task.cancel()
            try:
                await ws_listener_task
            except asyncio.CancelledError:
                pass
                
    except Exception as e:
        logger.exception("WebSocket startup exception.")
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("WebSocket connection closed.")

# Mount frontend files at the root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

if os.path.exists(FRONTEND_DIR):
    logger.info(f"Serving static frontend files from: {FRONTEND_DIR}")
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    logger.error(f"Frontend directory not found: {FRONTEND_DIR}")
