// ==========================================================================
// ZF-Grabber UI Logic (Vanilla JS, Glass Theme Interface)
// ==========================================================================

// 静态嵌入的 Python 核心抢课代码
const GRABBER_PYTHON_TEMPLATE = `# -*- coding: utf-8 -*-
"""
正方教务系统通用抢课脚本 (Universal Zhengfang JWGLXT Course Selector)
支持类型: 通识课、体育课
支持过滤: 课程号、老师名、上课时间、上课地点
开源协议: MIT License
"""

import os
import re
import sys
import time
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
import ddddocr

# 配置日志输出
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(threadName)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("Grabber")

# 默认配置（如果不存在 config.json，将使用此默认值）
DEFAULT_CONFIG = {
    "base_url": "",
    "username": "",
    "password": "",
    "xkxnm": "",
    "xkxqm": "12",
    "retry_delay": 1.0,
    "tasks": []
}

# 请求头
HEADERS = {
    "Host": "",
    "Origin": "",
    "Referer": "",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

class ZhengfangGrabber:
    def __init__(self, config):
        self.config = config
        self.base_url = config["base_url"].rstrip('/')
        self.username = config["username"]
        self.password = config["password"]
        self.xkxnm = config["xkxnm"]
        self.xkxqm = config["xkxqm"]
        self.retry_delay = config.get("retry_delay", 1.0)
        self.tasks = config.get("tasks", [])
        
        # 年级代码 (njdm_id) 取学号前4位
        self.year = self.username[:4] if len(self.username) >= 4 else "2024"
        
        # 填充请求头域名和来源
        domain = self.base_url.split("//")[-1].split("/")[0]
        HEADERS["Host"] = domain
        HEADERS["Origin"] = self.base_url
        HEADERS["Referer"] = f"{self.base_url}/jwglxt/xtgl/login_slogin.html"
        
        self.session = None
        self.cookies = {}
        self.params_tongshi = None # (kklxdm, xkkz_id)
        self.params_tiyu = None    # (kklxdm, xkkz_id)
        
        self.ocr = ddddocr.DdddOcr(show_ad=False)
        self.lock = threading.Lock()
        self.success_count = 0
        
    def login(self):
        """登录流程"""
        logger.info("开始尝试登录正方教务系统...")
        while True:
            self.session = requests.Session()
            try:
                # 1. 获取登录页面和 csrftoken
                login_url = f"{self.base_url}/jwglxt/xtgl/login_slogin.html"
                r1 = self.session.get(
                    login_url,
                    params={"time": int(time.time() * 1000)},
                    headers=HEADERS,
                    verify=False,
                    timeout=10
                )
                
                # 正则匹配 csrftoken
                token_match = re.search(
                    r'<input\\s+type="hidden"\\s+id="csrftoken"\\s+name="csrftoken"\\s+value="([^"]*)"',
                    r1.text
                )
                if not token_match:
                    logger.warning("未能在登录页面中找到 csrftoken，正在重试...")
                    time.sleep(self.retry_delay)
                    continue
                csrftoken = token_match.group(1)
                
                # 2. 获取并识别验证码
                kaptcha_url = f"{self.base_url}/jwglxt/kaptcha"
                captcha_data = self.session.get(
                    kaptcha_url,
                    params={"time": int(time.time() * 1000)},
                    headers=HEADERS,
                    verify=False,
                    timeout=10
                ).content
                
                captcha_code = self.ocr.classification(captcha_data)
                
                # 3. 提交登录
                post_data = {
                    "csrftoken": csrftoken,
                    "language": "zh_CN",
                    "yhm": self.username,
                    "mm": self.password,
                    "yzm": captcha_code,
                }
                
                r2 = self.session.post(
                    login_url,
                    headers=HEADERS,
                    params={"time": int(time.time() * 1000)},
                    data=post_data,
                    allow_redirects=False,
                    verify=False,
                    timeout=10
                )
                
                if r2.status_code == 302:
                    self.cookies = r2.cookies.get_dict()
                    logger.info(f"用户 {self.username} 登录成功！")
                    break
                else:
                    logger.warning("登录失败，正在重试...")
                    time.sleep(self.retry_delay)
                    
            except Exception as e:
                logger.error(f"登录异常: {e}，正在重试...")
                time.sleep(self.retry_delay)

    def fetch_course_controls(self):
        """解析通识课和体育课的控制ID (xkkz_id)"""
        logger.info("正在获取选课控制ID...")
        while True:
            try:
                index_url = f"{self.base_url}/jwglxt/xsxk/zzxkyzb_cxZzxkYzbIndex.html"
                ref_time = int(time.time() * 1000)
                
                headers = HEADERS.copy()
                headers["Referer"] = f"{self.base_url}/jwglxt/xtgl/index_initMenu.html?jsdm=&_t={ref_time}"
                
                response = self.session.get(
                    index_url,
                    headers=headers,
                    cookies=self.cookies,
                    params={"gnmkdm": "N253512", "layout": "default"},
                    verify=False,
                    timeout=10
                )
                
                html = response.text
                
                # 匹配 queryCourse(this,('kklxdm','xkkz_id'),'','')
                matches = re.findall(r"queryCourse\\(this,\\s*\\('([^']+)','([^']+)'\\)", html)
                if not matches:
                    logger.warning("未能在选课首页找到控制 ID，可能是未到选课时间，或登录已过期，正在重新登录...")
                    self.login()
                    time.sleep(self.retry_delay)
                    continue
                
                temp_tongshi = None
                temp_tiyu = None
                
                for kklxdm, xkkz_id in matches:
                    if kklxdm == "10":
                        temp_tongshi = (kklxdm, xkkz_id)
                    elif kklxdm == "15":
                        temp_tiyu = (kklxdm, xkkz_id)
                
                with self.lock:
                    self.params_tongshi = temp_tongshi
                    self.params_tiyu = temp_tiyu
                    
                logger.info(f"选课控制解析完毕 - 通识: {self.params_tongshi}, 体育: {self.params_tiyu}")
                break
                
            except Exception as e:
                logger.error(f"解析控制ID异常: {e}，正在重试...")
                time.sleep(self.retry_delay)

    def query_classes(self, kklxdm, kch_id, xkkz_id):
        """查询教学班"""
        query_url = f"{self.base_url}/jwglxt/xsxk/zzxkyzbjk_cxJxbWithKchZzxkYzb.html"
        data = {
            "bklx_id": "0",
            "njdm_id": self.year,
            "xkxnm": self.xkxnm,
            "xkxqm": self.xkxqm,
            "kklxdm": kklxdm,
            "kch_id": kch_id,
            "xkkz_id": xkkz_id,
        }
        params = {"gnmkdm": "N253512"}
        try:
            r = self.session.post(
                query_url,
                headers=HEADERS,
                cookies=self.cookies,
                params=params,
                data=data,
                verify=False,
                timeout=5
            )
            return r.json()
        except Exception as e:
            logger.debug(f"查询教学班异常: {e}")
            return None

    def select_class(self, jxb_ids, kch_id):
        """选课提交"""
        select_url = f"{self.base_url}/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html"
        data = {
            "jxb_ids": jxb_ids,
            "kch_id": kch_id,
            "qz": 0,
        }
        params = {"gnmkdm": "N253512"}
        try:
            r = self.session.post(
                select_url,
                headers=HEADERS,
                cookies=self.cookies,
                params=params,
                data=data,
                verify=False,
                timeout=5
            )
            return r.text
        except Exception as e:
            logger.debug(f"提交选课请求异常: {e}")
            return ""

    def run_task(self, task):
        """抢课任务执行逻辑"""
        task_type = task.get("type", "tongshi") # tongshi 或 tiyu
        kch_id = task["kch_id"]
        teacher = task.get("teacher", "")
        sksj = task.get("sksj", "")
        jxdd = task.get("jxdd", "")
        
        display_name = f"{'通识课' if task_type == 'tongshi' else '体育课'} - {kch_id}"
        logger.info(f"【{display_name}】抢课任务已启动...")
        
        while True:
            # 1. 查找对应的分类控制参数
            if task_type == "tongshi":
                ctrl = self.params_tongshi
            else:
                ctrl = self.params_tiyu
                
            if not ctrl:
                logger.warning(f"【{display_name}】未获取到类别【{task_type}】的控制ID，正在重新拉取...")
                self.fetch_course_controls()
                time.sleep(self.retry_delay)
                continue
                
            kklxdm, xkkz_id = ctrl
            
            # 2. 查询该课程下的教学班列表
            classes = self.query_classes(kklxdm, kch_id, xkkz_id)
            if not classes:
                time.sleep(self.retry_delay)
                continue
                
            # 3. 匹配教师、时间、上课地点
            target_class = None
            for cl in classes:
                jsxx = cl.get("jsxx", "") or ""
                sksj_val = cl.get("sksj", "") or ""
                jxdd_val = cl.get("jxdd", "") or ""
                
                # 教师名过滤：包含匹配
                teacher_match = (not teacher) or (teacher in jsxx)
                # 时间过滤：相等匹配
                sksj_match = (not sksj) or (sksj_val == sksj)
                # 地点过滤：相等匹配
                jxdd_match = (not jxdd) or (jxdd_val == jxdd)
                
                if teacher_match and sksj_match and jxdd_match:
                    target_class = cl
                    break
                    
            if not target_class:
                logger.warning(f"【{display_name}】没有匹配到符合条件的教学班 (教师: '{teacher}', 时间: '{sksj}', 地点: '{jxdd}')")
                time.sleep(self.retry_delay)
                continue
                
            jxb_ids = target_class.get("do_jxb_id")
            if not jxb_ids:
                logger.warning(f"【{display_name}】该教学班没有可用的 do_jxb_id")
                time.sleep(self.retry_delay)
                continue
                
            # 4. 发送选课请求
            res = self.select_class(jxb_ids, kch_id)
            logger.info(f"【{display_name}】选课返回: {res}")
            
            if '"flag":"1"' in res:
                logger.info(f"🎉【{display_name}】抢课成功！教师: {target_class.get('jsxx')}, 时间: {target_class.get('sksj')}, 地点: {target_class.get('jxdd')}")
                with self.lock:
                    self.success_count += 1
                break
            else:
                time.sleep(self.retry_delay)

    def start(self):
        """启动"""
        self.login()
        self.fetch_course_controls()
        
        logger.info(f"并发抢课任务开始，任务总数: {len(self.tasks)}")
        with ThreadPoolExecutor(max_workers=len(self.tasks), thread_name_prefix="GrabTask") as executor:
            executor.map(self.run_task, self.tasks)
            
        logger.info(f"抢课执行结束。成功抢到数量: {self.success_count}/{len(self.tasks)}")

def main():
    config = DEFAULT_CONFIG
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            logger.info(f"已加载配置文件: {config_path}")
        except Exception as e:
            logger.error(f"读取 {config_path} 失败: {e}，将使用默认配置。")
    else:
        logger.warning(f"配置文件不存在，将生成默认配置文件: {config_path}")
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"生成默认配置文件失败: {e}")
            
    grabber = ZhengfangGrabber(config)
    grabber.start()

if __name__ == "__main__":
    main()
`;

const README_MARKDOWN_TEMPLATE = `# Zhengfang Course Grabber (正方教务抢课通用脚本)

> **📌 历史版本与兼容性说明：**
> 本项目主要针对 **山东农业大学 (SDAU) 之前的老正方教务系统** 进行了开发与适配（该校目前已经更换升级教务系统，本代码对新系统不再适用）。
> 由于全国各高校的正方教务系统二次开发配置、登录验证方式（例如滑块拼图校验、双因素短信验证）以及密码参数前端加密算法存在很大差异，若在其他高校使用此脚本，通常需要根据实际接口对 \`grabber.py\` 中的 \`login\` 部分进行定制开发与修改（SDAU 旧版教务系统仅需要普通的图形验证码登录，脚本中内置了 \`ddddocr\` 本地自动识别）。

一个简单、高度并发、配置化的正方教务系统选课/抢课 Python 脚本。支持多线程并发、自动处理登录图形验证码、教务选课参数动态解析。

## ✨ 特性

- **分类支持**：内置通识课（类别10）与体育课（类别15）解析分支。
- **高精度过滤**：支持指定课程号、教师姓名（包含匹配）、上课时间（相等匹配）、上课地点（相等匹配）。
- **全自动图形验证码识别**：结合 \`ddddocr\` 无需第三方付费验证码 API 即可在本地高效识别。
- **动态选课控制解析**：脚本会自动抓取页面导航标签，自适应分析当前选课活动的控制符，不需要手动硬编码 \`xkkz_id\`。
- **并发多线程抢课**：使用 \`ThreadPoolExecutor\` 对所有添加的课程进行并行独立请求，极大提高高并发选课几率。

## 📦 项目结构

\`\`\`
├── grabber.py         # 抢课核心逻辑 (Python)
├── config.json         # 配置文件 (存储个人信息与抢课表)
└── requirements.txt    # 依赖声明文件
\`\`\`

## 🛠️ 环境准备

建议安装 Python 3.8+ 版本。

安装依赖项：
\`\`\`bash
pip install -r requirements.txt
\`\`\`

## 🚀 快速启动

1. 在抢课页面使用本项目提供的 UI 界面配置好学号密码与抢课信息。
2. 点击 **“下载项目”** 或是拷贝相应的文件内容到本地同一个文件夹内。
3. 在本地开启终端，执行以下命令开始抢课：
   \`\`\`bash
   python grabber.py
   \`\`\`
`;

const REQUIREMENTS_TEXT = `requests>=2.25.0
ddddocr>=1.4.7
`;

// 初始化 DOM 元素引用
const elements = {
    inputBaseUrl: document.getElementById('input-base-url'),
    inputUsername: document.getElementById('input-username'),
    inputPassword: document.getElementById('input-password'),
    inputXkxnm: document.getElementById('input-xkxnm'),
    inputXkxqm: document.getElementById('input-xkxqm'),
    inputRetryDelay: document.getElementById('input-retry-delay'),
    tasksContainer: document.getElementById('tasks-container'),
    emptyState: document.getElementById('empty-state'),
    btnAddTask: document.getElementById('btn-add-task'),
    btnCopyCode: document.getElementById('btn-copy-code'),
    btnDownloadFile: document.getElementById('btn-download-file'),
    btnDownloadAll: document.getElementById('btn-download-all'),
    codeConfigJson: document.getElementById('code-config-json'),
    codeGrabberPy: document.getElementById('code-grabber-py'),
    tabBtnConfig: document.getElementById('tab-btn-config'),
    tabBtnGrabber: document.getElementById('tab-btn-grabber'),
    tabBtnReadme: document.getElementById('tab-btn-readme')
};

// 任务数据状态
let tasks = [];

// 当前活动 Tab 标识
let activeTab = 'tab-config';

// 初始化函数
function init() {
    registerEvents();
    renderTasks();
    updateCodePreviews();
}

// 注册各种交互事件
function registerEvents() {
    // 监听全局输入框的输入变化
    const configInputs = [
        elements.inputBaseUrl,
        elements.inputUsername,
        elements.inputPassword,
        elements.inputXkxnm,
        elements.inputXkxqm,
        elements.inputRetryDelay
    ];
    configInputs.forEach(input => {
        input.addEventListener('input', () => {
            updateCodePreviews();
        });
    });

    // 添加任务按钮
    elements.btnAddTask.addEventListener('click', () => {
        addTask();
    });

    // Tab 切换逻辑
    const tabButtons = [elements.tabBtnConfig, elements.tabBtnGrabber, elements.tabBtnReadme];
    tabButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            tabButtons.forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
            
            const selectedTab = btn.getAttribute('data-tab');
            btn.classList.add('active');
            document.getElementById(selectedTab).classList.add('active');
            
            activeTab = selectedTab;
            
            // 如果切到运行指南，隐藏复制和单独下载按钮以简化操作
            if (activeTab === 'tab-readme') {
                elements.btnCopyCode.style.display = 'none';
                elements.btnDownloadFile.style.display = 'none';
            } else {
                elements.btnCopyCode.style.display = 'inline-flex';
                elements.btnDownloadFile.style.display = 'inline-flex';
            }
        });
    });

    // 复制当前预览的代码
    elements.btnCopyCode.addEventListener('click', () => {
        let codeText = '';
        if (activeTab === 'tab-config') {
            codeText = elements.codeConfigJson.innerText;
        } else if (activeTab === 'tab-grabber') {
            codeText = elements.codeGrabberPy.innerText;
        }
        
        navigator.clipboard.writeText(codeText).then(() => {
            const originalText = elements.btnCopyCode.innerHTML;
            elements.btnCopyCode.innerHTML = `<i class="fa-solid fa-check"></i> 已复制!`;
            elements.btnCopyCode.style.borderColor = 'var(--success)';
            elements.btnCopyCode.style.color = 'var(--success)';
            
            setTimeout(() => {
                elements.btnCopyCode.innerHTML = originalText;
                elements.btnCopyCode.style.borderColor = '';
                elements.btnCopyCode.style.color = '';
            }, 2000);
        }).catch(err => {
            alert('复制失败，请手动选取代码进行复制。');
        });
    });

    // 下载当前预览的单个文件
    elements.btnDownloadFile.addEventListener('click', () => {
        let content = '';
        let filename = '';
        if (activeTab === 'tab-config') {
            content = elements.codeConfigJson.innerText;
            filename = 'config.json';
        } else if (activeTab === 'tab-grabber') {
            content = GRABBER_PYTHON_TEMPLATE;
            filename = 'grabber.py';
        }
        if (content && filename) {
            downloadBlob(content, filename, 'text/plain');
        }
    });

    // 一键打包下载全部
    elements.btnDownloadAll.addEventListener('click', () => {
        generateZip();
    });
}

// 渲染任务卡片列表
function renderTasks() {
    elements.tasksContainer.innerHTML = '';
    
    if (tasks.length === 0) {
        elements.emptyState.style.display = 'flex';
    } else {
        elements.emptyState.style.display = 'none';
        
        tasks.forEach((task, index) => {
            const taskEl = document.createElement('div');
            taskEl.className = 'task-item';
            taskEl.dataset.id = task.id;
            
            taskEl.innerHTML = `
                <div class="task-item-header">
                    <span class="task-title"><i class="fa-solid fa-graduation-cap"></i> 课程 #${index + 1}</span>
                    <button type="button" class="btn-remove-task" onclick="removeTask('${task.id}')" title="删除该课程">
                        <i class="fa-solid fa-trash-can"></i> 删除
                    </button>
                </div>
                
                <!-- 行1：课程类别 (Type) -->
                <div class="task-row">
                    <div class="form-group no-margin">
                        <div class="input-wrapper">
                            <i class="fa-solid fa-tags input-icon"></i>
                            <select class="task-type">
                                <option value="tongshi" ${task.type === 'tongshi' ? 'selected' : ''}>通识课 (10)</option>
                                <option value="tiyu" ${task.type === 'tiyu' ? 'selected' : ''}>体育课 (15)</option>
                            </select>
                        </div>
                    </div>
                </div>
                
                <!-- 行2：课程号 (kch_id) -->
                <div class="task-row">
                    <div class="form-group no-margin">
                        <div class="input-wrapper">
                            <i class="fa-solid fa-fingerprint input-icon"></i>
                            <input type="text" class="task-kch-id" placeholder="课程号 kch_id (如 XT108010)" value="${task.kch_id}">
                        </div>
                    </div>
                </div>
                
                <!-- 分割线与过滤规则提示 -->
                <div class="filter-divider">
                    <span>过滤规则 (可选)</span>
                </div>
                
                <!-- 行3：老师名 (teacher) + 上课时间 (sksj) -->
                <div class="task-row task-row-2col">
                    <div class="form-group no-margin">
                        <div class="input-wrapper">
                            <i class="fa-solid fa-chalkboard-user input-icon"></i>
                            <input type="text" class="task-teacher" placeholder="教师 (如 董航)" value="${task.teacher || ''}">
                        </div>
                    </div>
                    <div class="form-group no-margin">
                        <div class="input-wrapper">
                            <i class="fa-solid fa-calendar-day input-icon"></i>
                            <input type="text" class="task-sksj" placeholder="上课时间 (如 星期三第5-6节)" value="${task.sksj || ''}">
                        </div>
                    </div>
                </div>
                
                <!-- 行4：上课地点 (jxdd) -->
                <div class="task-row">
                    <div class="form-group no-margin">
                        <div class="input-wrapper">
                            <i class="fa-solid fa-location-dot input-icon"></i>
                            <input type="text" class="task-jxdd" placeholder="上课地点 (如 5N101&lt;br/&gt;5N101)" value="${task.jxdd || ''}">
                        </div>
                    </div>
                </div>
            `;
            
            // 绑定事件，实时监听内容变化更新 JSON 预览
            const inputs = taskEl.querySelectorAll('input, select');
            inputs.forEach(input => {
                input.addEventListener('input', () => {
                    saveTasksState();
                    updateCodePreviews();
                });
            });
            
            elements.tasksContainer.appendChild(taskEl);
        });
    }
    
    updateCodePreviews();
}

// 添加一个新任务
function addTask() {
    const newId = 'task_' + Date.now() + '_' + (tasks.length + 1);
    tasks.push({
        id: newId,
        type: 'tongshi',
        kch_id: '',
        teacher: '',
        sksj: '',
        jxdd: ''
    });
    renderTasks();
}

// 删除一个任务
window.removeTask = function(taskId) {
    tasks = tasks.filter(t => t.id !== taskId);
    renderTasks();
};

// 从 DOM 表单中同步任务数组状态
function saveTasksState() {
    const taskElements = elements.tasksContainer.querySelectorAll('.task-item');
    tasks = Array.from(taskElements).map(el => {
        return {
            id: el.dataset.id,
            type: el.querySelector('.task-type').value,
            kch_id: el.querySelector('.task-kch-id').value,
            teacher: el.querySelector('.task-teacher').value,
            sksj: el.querySelector('.task-sksj').value,
            jxdd: el.querySelector('.task-jxdd').value
        };
    });
}

// 构建导出的 JSON 配置文件内容
function buildConfigJson() {
    return {
        base_url: elements.inputBaseUrl.value || "",
        username: elements.inputUsername.value || "",
        password: elements.inputPassword.value || "",
        xkxnm: elements.inputXkxnm.value || "",
        xkxqm: elements.inputXkxqm.value || "12",
        retry_delay: parseFloat(elements.inputRetryDelay.value) || 1.0,
        tasks: tasks.map(t => {
            return {
                type: t.type,
                kch_id: t.kch_id,
                teacher: t.teacher,
                sksj: t.sksj,
                jxdd: t.jxdd
            };
        })
    };
}

// 刷新右侧的代码预览区
function updateCodePreviews() {
    // 渲染 JSON
    const configObj = buildConfigJson();
    elements.codeConfigJson.textContent = JSON.stringify(configObj, null, 4);
    
    // 渲染 Python (静态展示)
    elements.codeGrabberPy.textContent = GRABBER_PYTHON_TEMPLATE;
}

// 下载原生 Blob 辅助函数
function downloadBlob(content, filename, contentType) {
    const blob = new Blob([content], { type: contentType });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    
    setTimeout(() => {
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    }, 0);
}

// 利用 JSZip 在客户端一键打包项目
function generateZip() {
    const zip = new JSZip();
    
    // 生成 config.json
    const configJsonText = JSON.stringify(buildConfigJson(), null, 4);
    
    // 向 zip 添加文件
    zip.file("config.json", configJsonText);
    zip.file("grabber.py", GRABBER_PYTHON_TEMPLATE);
    zip.file("requirements.txt", REQUIREMENTS_TEXT);
    zip.file("README.md", README_MARKDOWN_TEMPLATE);
    
    // 动画展示生成中状态
    const originalBtnHTML = elements.btnDownloadAll.innerHTML;
    elements.btnDownloadAll.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> 正在打包生成项目包...`;
    elements.btnDownloadAll.disabled = true;
    
    zip.generateAsync({ type: "blob" })
       .then(function(content) {
            const url = URL.createObjectURL(content);
            const link = document.createElement('a');
            link.href = url;
            link.download = "zf-grabber-project.zip";
            document.body.appendChild(link);
            link.click();
            
            setTimeout(() => {
                document.body.removeChild(link);
                URL.revokeObjectURL(url);
                elements.btnDownloadAll.innerHTML = originalBtnHTML;
                elements.btnDownloadAll.disabled = false;
            }, 1000);
       })
       .catch(err => {
           console.error("Zip generation error: ", err);
           alert("打包失败，请检查浏览器控制台报错。");
           elements.btnDownloadAll.innerHTML = originalBtnHTML;
           elements.btnDownloadAll.disabled = false;
       });
}

// 执行初始化
document.addEventListener('DOMContentLoaded', init);
