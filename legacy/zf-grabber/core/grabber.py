# -*- coding: utf-8 -*-
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
                    r'<input\s+type="hidden"\s+id="csrftoken"\s+name="csrftoken"\s+value="([^"]*)"',
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
                # 通识代码通常是 10，体育代码通常是 15
                matches = re.findall(r"queryCourse\(this,\s*\('([^']+)','([^']+)'\)", html)
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
                
                # 教师名过滤：包含匹配 (例如 "董航" in jsxx)
                teacher_match = (not teacher) or (teacher in jsxx)
                # 时间过滤：相等匹配 (例如 sksj_val == "星期三第5-6节{1-16周}")
                sksj_match = (not sksj) or (sksj_val == sksj)
                # 地点过滤：相等匹配 (例如 jxdd_val == "5N101<br/>5N101")
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
