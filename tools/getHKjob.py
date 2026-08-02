# ------------------------------------------------------------------------------------ #
# 工具: getHKjob
# 用途: 搜尋香港勞工處 (jobs.gov.hk) 職位空缺，提取公司聯絡資料（電話 / 電郵）
# 來源: 改寫自 getUser/getHKjobWeb.py（原 Colab + TG Bot 版本）
# 日期: 2026-08-02
# ------------------------------------------------------------------------------------ #

import os
import re
import time
import json
import requests
from typing import Optional, Union, List, Dict

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    TimeoutException as SeleniumTimeout,
    WebDriverException,
)
from lxml import html

# ------------------------------------------------------------------------------------ #
# PLUGIN_INFO - mokagi 工具註冊介面
# ------------------------------------------------------------------------------------ #
PLUGIN_INFO = {
    "command": "/hkjobs",
    "icon": "💼",
    "handler": "handle_hk_jobs",
    "description": "搜尋香港勞工處職位空缺，提取公司聯絡資料（電話/電郵）。",
    "intent_keywords": [
        ("/hkjobs", "/hkjobs"),
        ("/hkjobs", "/hkjobs"),
        ("/hkjobs", "/hkjobs"),
        ("香港搵工", "/hkjobs"),
        ("勞工處搵工", "/hkjobs"),
        ("找香港工作", "/hkjobs"),
        ("hkjob", "/hkjobs"),
        ("搵客", "/hkjobs"),
        ("找客戶", "/hkjobs"),
    ],
    "naturalize": True,
    "naturalize_func": "naturalize_hk_jobs_result",
    "tool_schema": {
        "name": "hkjobs",
        "description": (
            "搜尋香港勞工處 (jobs.gov.hk) 職位空缺，提取招聘公司的聯絡方式。\n\n"
            "【功能】給定關鍵字（如「售貨員」、「文員」、「司機」），"
            "自動搜尋勞工處職位空缺列表，逐一拜訪每個職位頁面，"
            "提取公司名稱以及電話/電郵聯絡方式，去重後返回結果。\n\n"
            "【返回格式】\n"
            "- 成功時返回 JSON：{\"success\": true, \"keyword\": \"...\", \"total\": N, "
            "\"results\": [{\"company\": \"公司名\", \"contact\": \"電話或電郵\"}, ...]}\n"
            "- 失敗時返回 JSON：{\"success\": false, \"error\": \"錯誤訊息\"}\n\n"
            "【注意】\n"
            "- 需要主機安裝 Chromium + Selenium（若未安裝會自動提示）\n"
            "- 每次搜尋約需 30 秒至數分鐘，視乎結果數量\n"
            "- 勞工處網站可能因頻繁請求而暫時封鎖 IP"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "搜尋關鍵字，例如「售貨員」、「文員」、「司機」、「洗碗」。"
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多回傳的公司數量（預設 10，上限 50）。",
                    "default": 10,
                },
                "phone_prefix": {
                    "type": "string",
                    "description": "電話號碼開頭篩選，預設 '4569'（香港常見職位查詢熱線前綴）。",
                    "default": "4569",
                },
                "phone_digits": {
                    "type": "integer",
                    "description": "電話號碼位數，預設 8。",
                    "default": 8,
                },
            },
            "required": ["keyword"],
        },
    },
}


# ------------------------------------------------------------------------------------ #
# Chrome 設定與輔助
# ------------------------------------------------------------------------------------ #

def _chrome_setup() -> webdriver.Chrome:
    """設定並啟動 Chrome（headless 模式，適合伺服器環境）。"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    options.add_argument("--lang=zh-HK")

    try:
        driver = webdriver.Chrome(options=options)
        return driver
    except WebDriverException:
        # 可能在非 Chrome 環境，嘗試其他寫法
        pass
    raise RuntimeError("無法啟動 Chrome 瀏覽器，請確認已安裝 chromium 和 chromedriver。")


def _safe_click(driver, label: str, xpath: str, timeout: int = 10) -> bool:
    """安全點擊元素。"""
    try:
        element = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        )
        element.click()
        return True
    except Exception:
        return False


def _safe_type(driver, label: str, xpath: str, text: str, timeout: int = 10) -> bool:
    """安全輸入文字。"""
    try:
        element = WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((By.XPATH, xpath))
        )
        element.clear()
        element.send_keys(text)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------------------------ #
# 聯絡方式提取
# ------------------------------------------------------------------------------------ #

def _extract_contacts(table_text: str, phone_prefix: str, phone_digits: int):
    """從表格文字中提取電話號碼和電郵。"""
    # 全形數字 → 半形
    table_text = "".join(
        chr(ord(c) - 65248) if "０" <= c <= "９" else c for c in table_text
    )
    # 清理空白
    table_text = re.sub(r"[\n\r\u3000\xa0]+", " ", table_text)
    table_text = re.sub(r"\s+", " ", table_text)

    # 電話：以指定前綴開頭、指定位數
    phone_pattern = rf"(?<!\d)([{phone_prefix}]\d{{{phone_digits}}})(?!\d)"
    phones = re.findall(phone_pattern, table_text)

    # 電郵
    email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    emails = re.findall(email_pattern, table_text, flags=re.IGNORECASE)

    return phones, emails


def _fetch_company_contact(
    url: str,
    company_xpath: str,
    table_xpath: str,
    phone_prefix: str,
    phone_digits: int,
) -> Optional[str]:
    """訪問單一職位頁面，提取公司名稱和聯絡方式。
    返回格式：「公司名=聯絡方式」或 None。
    """
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        tree = html.fromstring(resp.content)

        # 公司名
        company_names = tree.xpath(company_xpath)
        if not company_names:
            return None
        company_name = str(company_names[0]).strip()
        # 清理公司名：保留中英數及底線連字號
        company_name = re.sub(r"\s", "_", company_name)
        company_name = re.sub(
            r"[^a-zA-Z0-9_\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff-]",
            "",
            company_name,
            flags=re.UNICODE,
        )
        if not company_name:
            return None

        # 表格內容
        job_tables = tree.xpath(table_xpath)
        if not job_tables:
            return None
        table_text = job_tables[0].text_content()

        phones, emails = _extract_contacts(table_text, phone_prefix, phone_digits)

        if phones:
            return f"{company_name}={phones[0]}"
        elif emails:
            return f"{company_name}={emails[0]}"
        return None

    except Exception:
        return None


def _deduplicate_contacts(results: List[str]) -> List[str]:
    """依聯絡方式去重（保留首次出現）。"""
    seen = set()
    filtered = []
    for entry in results:
        if "=" in entry:
            contact = entry.split("=", 1)[1]
            if contact not in seen:
                seen.add(contact)
                filtered.append(entry)
    return filtered


# ------------------------------------------------------------------------------------ #
# 勞工處搜尋
# ------------------------------------------------------------------------------------ #

# 勞工處 XPath 定義
LABOUR_XPATH = {
    "左上資料數": '//*[@id="content-innerdiv"]/div[*]/strong[1]',
    "下一頁按鈕": '//*[@id="swapNextPage"]',
    "列表顯示": '//*[@id="content-innerdiv"]/div[1]/div/div[2]/a',
    "公司名xpath": '//*[@id="empName"]/text()',
    "表格xpath": '//*[@id="jobOrderTable"]',
    "工作列表": '//*[@id="job_list_table"]/tbody',
    "工作列表中的所有href": '//*[@id="job_list_table"]//a[contains(@id, "_orderNo_hyper")]',
    "關鍵字輸入框": '//*[@id="simp_searchKeyword"]',
    "搜尋空缺按鈕": '//*[@id="btnSearch"]',
}


def _scrape_labour_gov(
    keyword: str,
    max_results: int = 10,
    phone_prefix: str = "4569",
    phone_digits: int = 8,
) -> Dict:
    """核心：搜尋香港勞工處並提取公司聯絡資料。"""
    driver = None
    try:
        driver = _chrome_setup()
        driver.maximize_window()

        company_xpath = LABOUR_XPATH["公司名xpath"]
        table_xpath = LABOUR_XPATH["表格xpath"]

        # 嘗試不同子域名
        subdomains = ["www", "www1", "www2", "www3", "www4"]
        labour_url = ""
        for sub in subdomains:
            test_url = f"https://{sub}.jobs.gov.hk/0/tc/jobseeker/jobsearch/joblist/"
            try:
                driver.get(test_url)
                labour_url = test_url
                break
            except Exception:
                continue

        if not labour_url:
            return {"success": False, "error": "無法連線到勞工處網站（所有子域名均失敗）。"}

        driver.get(labour_url)

        # 輸入關鍵字並搜尋
        if not _safe_type(driver, "關鍵字輸入框", LABOUR_XPATH["關鍵字輸入框"], keyword):
            return {"success": False, "error": "無法找到關鍵字輸入框，勞工處網站可能已改版。"}

        if not _safe_click(driver, "搜尋空缺按鈕", LABOUR_XPATH["搜尋空缺按鈕"]):
            return {"success": False, "error": "無法點擊搜尋按鈕。"}

        # 切換到列表顯示
        _safe_click(driver, "列表顯示", LABOUR_XPATH["列表顯示"])

        current_url = driver.current_url

        # 取得總資料數
        try:
            count_elem = WebDriverWait(driver, 9).until(
                EC.visibility_of_element_located((By.XPATH, LABOUR_XPATH["左上資料數"]))
            )
            total_count = int(count_elem.text.strip())
        except Exception:
            total_count = 0

        if total_count == 0:
            driver.quit()
            return {"success": True, "keyword": keyword, "total": 0, "results": []}

        all_results = []
        page = 1
        per_page = 20

        while True:
            # 等待工作列表
            try:
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, LABOUR_XPATH["工作列表"]))
                )
            except Exception:
                break

            rows = driver.find_elements(By.XPATH, LABOUR_XPATH["工作列表中的所有href"])
            for row in rows:
                job_url = row.get_attribute("href")
                contact_entry = _fetch_company_contact(
                    job_url, company_xpath, table_xpath, phone_prefix, phone_digits
                )
                if contact_entry:
                    all_results.append(contact_entry)
                    if len(all_results) >= max_results:
                        break

            if len(all_results) >= max_results:
                break

            # 換頁
            if total_count > per_page:
                per_page += 20
                page += 1
                try:
                    driver.get(f"{current_url}&page={page}")
                except Exception:
                    break
            else:
                break

        driver.quit()
        driver = None

        # 去重
        all_results = _deduplicate_contacts(all_results)
        # 限制數量
        all_results = all_results[:max_results]

        # 格式化輸出
        formatted = []
        for entry in all_results:
            if "=" in entry:
                parts = entry.split("=", 1)
                formatted.append({"company": parts[0], "contact": parts[1]})

        return {
            "success": True,
            "keyword": keyword,
            "total": len(formatted),
            "results": formatted,
        }

    except Exception as e:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        return {"success": False, "error": f"搜尋過程發生錯誤：{str(e)}"}


# ------------------------------------------------------------------------------------ #
# Handler - mokagi 呼叫入口
# ------------------------------------------------------------------------------------ #

async def handle_hk_jobs(
    args: Union[str, dict],
    chat_id: str = None,
    agent_config: Optional[Dict] = None,
) -> str:
    """
    處理香港勞工處職位搜尋請求。

    支援：
    - dict 參數（LLM 透過 tool_schema 調用）
    - 字串參數（命令列 /hkjobs 關鍵字 [max_results]）
    """
    # ── 解析參數 ──
    keyword = None
    max_results = 10
    phone_prefix = "4569"
    phone_digits = 8

    if isinstance(args, dict):
        keyword = args.get("keyword", "").strip()
        max_results = int(args.get("max_results", 10))
        phone_prefix = args.get("phone_prefix", "4569")
        phone_digits = int(args.get("phone_digits", 8))
    elif isinstance(args, str):
        parts = args.strip().split()
        if not parts:
            return json.dumps(
                {"success": False, "error": "請提供搜尋關鍵字。用法：/hkjobs <關鍵字> [最大筆數]"},
                ensure_ascii=False,
            )
        keyword = parts[0]
        if len(parts) > 1:
            try:
                max_results = int(parts[1])
            except ValueError:
                pass

    if not keyword:
        return json.dumps(
            {"success": False, "error": "請提供搜尋關鍵字。"},
            ensure_ascii=False,
        )

    # 安全限制
    max_results = min(max(max_results, 1), 50)

    # ── 執行搜尋 ──
    result = _scrape_labour_gov(keyword, max_results, phone_prefix, phone_digits)
    return json.dumps(result, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------------------------ #
# Naturalize - 結果自然語言化
# ------------------------------------------------------------------------------------ #

def naturalize_hk_jobs_result(result_str: str) -> str:
    """將 JSON 結果轉換為自然語言訊息。"""
    try:
        data = json.loads(result_str)
    except (json.JSONDecodeError, TypeError):
        return result_str

    if not data.get("success"):
        return f"❌ 搜尋失敗：{data.get('error', '未知錯誤')}"

    keyword = data.get("keyword", "?")
    total = data.get("total", 0)
    results = data.get("results", [])

    if total == 0:
        return f"🔍 搜尋「{keyword}」沒有找到任何公司聯絡資料，可能沒有相關職位空缺。"

    lines = [f"💼 搜尋「{keyword}」找到 **{total}** 間公司聯絡資料：\n"]
    for i, r in enumerate(results, 1):
        company = r.get("company", "?")
        contact = r.get("contact", "?")
        icon = "📧" if "@" in contact else "📞"
        lines.append(f"{i}. {icon} **{company}** → `{contact}`")

    return "\n".join(lines)


# ------------------------------------------------------------------------------------ #
# CLI 測試入口
# ------------------------------------------------------------------------------------ #
if __name__ == "__main__":
    import asyncio

    async def _test():
        print("🧪 測試 getHKjob.py ...")
        result = await handle_hk_jobs({"keyword": "文員", "max_results": 3})
        print(result)
        print("---自然化---")
        print(naturalize_hk_jobs_result(result))

    asyncio.run(_test())
