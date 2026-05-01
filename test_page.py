import re

captured_url = "https://22bet.com.gh/line/event/list?count=50"
for pg in range(2, 4):
    if 'page=' in captured_url:
        pg_url = re.sub(r'page=\d+', f'page={pg}', captured_url)
    else:
        sep = '&' if '?' in captured_url else '?'
        pg_url = f"{captured_url}{sep}page={pg}"
    print(pg_url)
