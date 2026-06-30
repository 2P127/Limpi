import asyncio
import hashlib
import json
import re
import random
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


EGO_GIFT_FIELDNAMES = [
    "이름", "등급", "키워드", "카테고리", "연관", "첫_등장",
    "강화_가능", "판매_가격", "구매_가능", "합성_기프트",
    "하드_한정", "익스트림_한정", "테마팩_한정",
    "조합식", "효과", "이미지_URL",
]
DEFAULT_EGO_GIFT_STORE_PATH = Path("ego_gifts.json")


URLS = [
    ("기본 7키워드", "https://namu.wiki/w/Limbus%20Company/%EA%B1%B0%EC%9A%B8%20%EB%8D%98%EC%A0%84/E.G.O%20%EA%B8%B0%ED%94%84%ED%8A%B8/%EA%B8%B0%EB%B3%B8%207%ED%82%A4%EC%9B%8C%EB%93%9C%20%EA%B8%B0%ED%94%84%ED%8A%B8"),
    ("7키워드 외",  "https://namu.wiki/w/Limbus%20Company/%EA%B1%B0%EC%9A%B8%20%EB%8D%98%EC%A0%84/E.G.O%20%EA%B8%B0%ED%94%84%ED%8A%B8/%EA%B8%B0%EB%B3%B8%207%ED%82%A4%EC%9B%8C%EB%93%9C%20%EC%99%B8%20%EA%B8%B0%ED%94%84%ED%8A%B8"),
    ("팩 전용",    "https://namu.wiki/w/Limbus%20Company/%EA%B1%B0%EC%9A%B8%20%EB%8D%98%EC%A0%84/E.G.O%20%EA%B8%B0%ED%94%84%ED%8A%B8/%ED%8A%B9%EC%A0%95%20%ED%85%8C%EB%A7%88%ED%8C%A9%20%EC%A0%84%EC%9A%A9%20%EA%B8%B0%ED%94%84%ED%8A%B8"),
]


EXTRACT_JS = """
() => {
    const results = [];
    const tables = Array.from(document.querySelectorAll('table')).filter(t =>
        t.innerText.includes('기프트 상세 정보')
    );

    // 올바른 키워드 목록 (실제 페이지에서 확인된 11종 + EX)
    const KEYWORDS = [
        '화상','출혈','진동','파열','침잠',
        '호흡','충전','참격','관통','타격',
        '범용','EX'
    ];

    // 텍스트가 어떤 키워드인지 판별
    // - 'EX'는 짧으므로 정확히 일치하거나 단독 토큰일 때만 인정
    // - 그 외는 부분 포함 허용
    const matchKeyword = (txt) => {
        if (!txt) return '';
        if (txt === 'EX') return 'EX';
        if (/(^|\\s)EX(\\s|$)/.test(txt)) return 'EX';
        for (const k of KEYWORDS) {
            if (k === 'EX') continue;
            if (txt.includes(k)) return k;
        }
        return '';
    };

    for (const table of tables) {
        try {
            const rows = table.querySelectorAll('tr');
            if (rows.length < 1) continue;

            // ── 이름 & 등급 ───────────────────────────────────────────
            const td1 = rows[0].querySelectorAll('td')[1];
            if (!td1) continue;
            const outerDiv = td1.querySelector('div');
            if (!outerDiv) continue;

            const nameDiv = outerDiv.querySelector('div');
            const rawName = nameDiv
                ? nameDiv.innerText.trim()
                : outerDiv.innerText.split('\\n')[0].trim();

            const gradeMatch = rawName.match(/^([ⅠⅡⅢⅣⅤ]|[1-5])\\s+/);
            const grade = gradeMatch ? gradeMatch[1] : '';
            const name  = rawName.replace(/^([ⅠⅡⅢⅣⅤ]|[1-5])\\s+/, '').trim();

            // ── outerDiv 직계 자식 배열 ───────────────────────────────
            const ch = Array.from(outerDiv.children);

            // 첫 번째 BR 인덱스 = 이름 줄의 끝
            // (이름+키워드는 이 BR 앞에 모여 있음)
            let firstBrIdx = ch.findIndex(c => c.tagName === 'BR');
            if (firstBrIdx === -1) firstBrIdx = ch.length;

            // ── 키워드 추출 (페이지 공통 규칙) ────────────────────────
            // 첫 BR 이전에서, 이미지 없고 숫자가 아닌 SPAN을 찾아 키워드 매칭
            let keyword = '';
            for (let i = 0; i < firstBrIdx; i++) {
                const c = ch[i];
                if (c.tagName !== 'SPAN') continue;
                if (c.querySelector('img')) continue;     // 아이콘 span 제외
                const txt = c.innerText.trim();
                if (!txt) continue;
                if (/^\\d+$/.test(txt)) continue;          // 순수 숫자 제외
                const matched = matchKeyword(txt);
                if (matched) { keyword = matched; break; }
            }

            // ── 판매 가격 ─────────────────────────────────────────────
            // "코스트" 아이콘을 품은 SPAN의 바로 다음 SPAN이 가격
            let priceRaw = '';
            for (let i = 0; i < ch.length; i++) {
                if (ch[i].tagName !== 'SPAN') continue;
                const imgs = ch[i].querySelectorAll('img');
                let isCoin = false;
                for (const img of imgs) {
                    if ((img.alt || '').includes('코스트')) { isCoin = true; break; }
                }
                if (isCoin && i + 1 < ch.length && ch[i+1].tagName === 'SPAN') {
                    priceRaw = ch[i+1].innerText.trim();
                    break;
                }
            }
            const price = priceRaw.replace(/\\[구매불가\\]/g, '').trim();
            const buyable = priceRaw.includes('구매불가') ? 'X' : 'O';

            // ── 하드 / 익스트림 / 합성 ────────────────────────────────
            const allFlagText = ch
                .filter(c => c.tagName === 'SPAN' || c.tagName === 'STRONG')
                .map(c => c.innerText.trim())
                .join(' ');

            const isHard    = allFlagText.includes('하드');
            const isExtreme = allFlagText.includes('익스트림') || allFlagText.includes('EXTREME');
            const isSynth   = allFlagText.includes('합성');

            // ── 테마팩 한정 정보 ──────────────────────────────────────
            let themePack = '';
            for (const c of ch) {
                if (c.tagName === 'STRONG') {
                    themePack = c.innerText.trim();
                    break;
                }
            }
            if (!themePack) {
                for (const c of ch) {
                    if (c.tagName !== 'SPAN') continue;
                    if (c.querySelector('img')) continue;
                    const txt = c.innerText.trim();
                    if (txt.includes('한정') || txt.includes('합성 기프트')) {
                        themePack = txt;
                        break;
                    }
                }
            }

            // ── 이미지 URL ─────────────────────────────────────────────
            const td0  = rows[0].querySelectorAll('td')[0];
            const imgs = td0 ? td0.querySelectorAll('img') : [];
            let imgUrl = '';
            if (imgs.length > 0) {
                const lastImg = imgs[imgs.length - 1];
                const src = lastImg.getAttribute('src') || '';
                imgUrl = src.startsWith('//') ? 'https:' + src : src;
            }

            // ── 나머지 행: 키-값 파싱 ────────────────────────────────
            const fields = { 연관: '', 첫_등장: '', 강화_가능: '', 효과: '', 조합식: '', 카테고리: '' };
            for (let i = 1; i < rows.length; i++) {
                const tds = rows[i].querySelectorAll('td');
                if (tds.length < 2) continue;
                const key = tds[0].innerText.trim();
                const val = tds[1].innerText.trim();
                if (key.includes('연관'))     fields.연관     = val;
                if (key.includes('등장'))     fields.첫_등장  = val;
                if (key.includes('강화'))     fields.강화_가능 = val;
                if (key.includes('효과'))     fields.효과     = val;
                if (key.includes('조합'))     fields.조합식   = val;
                if (key.includes('카테고리')) fields.카테고리 = val;
            }

            results.push({
                이름:        name,
                등급:        grade,
                키워드:      keyword,
                카테고리:    fields.카테고리,
                연관:        fields.연관,
                첫_등장:     fields.첫_등장,
                강화_가능:   fields.강화_가능,
                판매_가격:   price,
                구매_가능:   buyable,
                합성_기프트: isSynth   ? 'O' : '',
                하드_한정:   isHard    ? 'O' : '',
                익스트림_한정: isExtreme ? 'O' : '',
                테마팩_한정: themePack,
                조합식:      fields.조합식,
                효과:        fields.효과,
                이미지_URL:  imgUrl,
            });
        } catch (e) {
            results.push({ 이름: '파싱오류: ' + e.message });
        }
    }
    return results;
}
"""


def _log(verbose: bool, message: str) -> None:
    if verbose:
        print(message)


async def goto_with_retry(page, url, max_retries=3, *, verbose=True):
    for attempt in range(1, max_retries + 1):
        try:
            _log(verbose, f"   접속 시도 {attempt}/{max_retries}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=120000)
            wait_ms = random.randint(3000, 5000)
            await page.wait_for_timeout(wait_ms)
            return True
        except PlaywrightTimeoutError:
            _log(verbose, f"   ⚠️  타임아웃 (시도 {attempt}/{max_retries})")
            if attempt < max_retries:
                sleep_sec = attempt * 5
                _log(verbose, f"   {sleep_sec}초 후 재시도...")
                await asyncio.sleep(sleep_sec)
            else:
                _log(verbose, f"   ❌ {max_retries}회 모두 실패")
                return False


def normalize_ego_gift_row(row):
    return {field: str(row.get(field) or "").strip() for field in EGO_GIFT_FIELDNAMES}


def normalize_ego_gift_rows(rows):
    normalized = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        gift = normalize_ego_gift_row(row)
        name = gift["이름"]
        if not name or name.startswith("파싱오류"):
            continue
        key = (name, gift["등급"], gift["키워드"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(gift)
    return normalized


def ego_gift_rows_hash(rows):
    payload = json.dumps(
        normalize_ego_gift_rows(rows),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_ego_gift_store(path=DEFAULT_EGO_GIFT_STORE_PATH):
    path = Path(path)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        gifts = normalize_ego_gift_rows(payload)
        return {
            "version": 1,
            "updated_at": "",
            "source": "legacy-list",
            "content_hash": ego_gift_rows_hash(gifts),
            "gifts": gifts,
        }
    return None


def write_ego_gift_store(path, rows):
    path = Path(path)
    gifts = normalize_ego_gift_rows(rows)
    content_hash = ego_gift_rows_hash(gifts)
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "ego.py",
        "content_hash": content_hash,
        "count": len(gifts),
        "gifts": gifts,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp_path.replace(path)
    return payload


def _log_ego_gift_summary(gifts, *, verbose=True):
    keyword_dist = {}
    for gift in gifts:
        keyword = gift.get("키워드") or "(없음)"
        keyword_dist[keyword] = keyword_dist.get(keyword, 0) + 1
    _log(verbose, "\n📊 키워드 분포:")
    for keyword, count in sorted(keyword_dist.items(), key=lambda item: -item[1]):
        _log(verbose, f"   {keyword}: {count}개")


async def crawl_ego_gifts(*, verbose=True):
    all_gifts = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="ko-KR",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()

        for label, url in URLS:
            _log(verbose, f"\n📄 [{label}] 수집 시작")

            success = await goto_with_retry(page, url, max_retries=3, verbose=verbose)
            if not success:
                _log(verbose, f"   [{label}] 페이지 로드 실패 - 건너뜁니다")
                continue

            gifts = await page.evaluate(EXTRACT_JS)
            _log(verbose, f"   기프트 {len(gifts)}개 파싱 완료")

            price_ok   = sum(1 for g in gifts if g.get("판매_가격"))
            img_ok     = sum(1 for g in gifts if g.get("이미지_URL"))
            keyword_ok = sum(1 for g in gifts if g.get("키워드"))
            theme_ok   = sum(1 for g in gifts if g.get("테마팩_한정"))
            fails      = [g for g in gifts if g.get("이름", "").startswith("파싱오류")]

            _log(verbose, f"   키워드 수집: {keyword_ok}/{len(gifts)}개")
            _log(verbose, f"   가격 수집: {price_ok}/{len(gifts)}개")
            _log(verbose, f"   이미지 URL: {img_ok}/{len(gifts)}개")
            if theme_ok:
                _log(verbose, f"   테마팩 한정 정보: {theme_ok}개")
            if fails:
                _log(verbose, f"   ⚠️  파싱 실패: {len(fails)}개")

            all_gifts.extend(gifts)

            if label != URLS[-1][0]:
                delay = random.randint(3, 7)
                _log(verbose, f"   다음 페이지까지 {delay}초 대기...")
                await asyncio.sleep(delay)

        await browser.close()

    gifts = normalize_ego_gift_rows(all_gifts)
    _log_ego_gift_summary(gifts, verbose=verbose)
    return gifts


async def crawl(output_path=DEFAULT_EGO_GIFT_STORE_PATH, *, verbose=True):
    gifts = await crawl_ego_gifts(verbose=verbose)
    payload = write_ego_gift_store(output_path, gifts)
    _log(verbose, f"\n✅ 완료! 총 {payload['count']}개 → {Path(output_path)} 저장됨")
    return payload


if __name__ == "__main__":
    asyncio.run(crawl())
