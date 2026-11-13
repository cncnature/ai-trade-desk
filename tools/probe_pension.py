"""종목별 연기금 순매수를 주는 공개 출처가 있는지 확인 (응답 키만 출력)."""
import json
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
tests = [
    ("daum days", "https://finance.daum.net/api/investor/days?page=1&perPage=3&symbolCode=A005930&pagination=true",
     {"Referer": "https://finance.daum.net/quotes/A005930"}),
    ("naver m trend", "https://m.stock.naver.com/api/stock/005930/trend?pageSize=3", {}),
    ("naver m investor", "https://m.stock.naver.com/api/stock/005930/investor?pageSize=3", {}),
    ("naver m theme", "https://m.stock.naver.com/api/stocks/theme?page=1&pageSize=3", {}),
]
for name, url, h in tests:
    try:
        r = requests.get(url, headers={**UA, **h}, timeout=15)
        txt = r.text[:600]
        print(f"== {name} {r.status_code}\n{txt}\n")
    except Exception as ex:
        print(f"== {name} ERR {ex}")
