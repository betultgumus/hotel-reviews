"""Trip.com otel yorumlarini Selenium ile CSV dosyasina kaydeder.

Kullanim:
    python trip_yorum.py
    python trip_yorum.py "https://www.trip.com/hotels/detail?hotelid=...#review"
    python trip_yorum.py --headless

Not: Trip.com zaman zaman insan dogrulamasi gosterebilir. Bu durumda tarayicida
dogrulamayi tamamlayin; program sayfanin yuklenmesini beklemeye devam eder.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from selenium import webdriver
from selenium.common.exceptions import (
    JavascriptException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# Baska bir oteli cekmek icin yalnizca bu URL'yi degistirebilirsiniz.
OTEL_URL = (
    "https://www.trip.com/hotels/detail?hotelid=3451898&Allianceid=810504"
    "&Sid=1394411&utm_medium=cpc&utm_campaign=HPA&utm_source=google#review"
)
CSV_DOSYASI = Path(__file__).with_name("trip_yorum.csv")

BEKLEME = 25
SAYFA_BEKLEME = 3
YORUM_SAYFASI_BEKLEME = 2
CSV_ALANLARI = (
    "otel_adi",
    "yorum",
    "puan",
    "yorum_tarihi",
    "konum",
    "konaklama_tarihi",
    "musteri_toplam_yorum_sayisi",
    "musteri_kademe",
    "seyahat_tipi",
    "oda_tipi",
)


@dataclass(frozen=True)
class YorumKaydi:
    otel_adi: str
    yorum: str
    puan: str
    yorum_tarihi: str
    konum: str
    konaklama_tarihi: str
    musteri_toplam_yorum_sayisi: str
    musteri_kademe: str
    seyahat_tipi: str
    oda_tipi: str


def temizle(metin: str | None) -> str:
    return re.sub(r"\s+", " ", metin or "").strip()


def tarayici_olustur(headless: bool) -> webdriver.Chrome:
    secenekler = webdriver.ChromeOptions()
    secenekler.add_argument("--lang=tr-TR")
    secenekler.add_argument("--start-maximized")
    secenekler.add_argument("--disable-notifications")
    secenekler.add_argument("--disable-blink-features=AutomationControlled")
    secenekler.add_experimental_option("excludeSwitches", ["enable-automation"])
    secenekler.add_experimental_option("useAutomationExtension", False)
    if headless:
        secenekler.add_argument("--headless=new")
        secenekler.add_argument("--window-size=1920,1080")

    tarayici = webdriver.Chrome(options=secenekler)
    tarayici.set_page_load_timeout(60)
    tarayici.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": (
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});"
            )
        },
    )
    return tarayici


def takvimi_kapat(driver: webdriver.Chrome) -> None:
    """Uc saniye sonra bos bir noktaya tiklayarak acik takvimi kapatir."""
    time.sleep(SAYFA_BEKLEME)
    try:
        govde = driver.find_element(By.TAG_NAME, "body")
        # Sol ustte, sayfa govdesinin bos kenarina tikla.
        ActionChains(driver).move_to_element_with_offset(govde, 5, 180).click().perform()
    except Exception:
        # Sayfa yerlesimi tiklamayi engellerse govdeye JS ile tikla.
        try:
            driver.execute_script(
                "document.activeElement && document.activeElement.blur();"
                "document.body.dispatchEvent(new MouseEvent('click', {bubbles:true}));"
            )
        except JavascriptException:
            pass


def show_more_butonlarini_ac(driver: webdriver.Chrome) -> int:
    """Sayfadaki yorumlara ait tum Show More dugmelerini acar.

    Dugmeye tiklamadan once ait oldugu yorum kartini isaretler. Boylece
    Trip.com'un rastgele uretilen CSS siniflari degisse de kart daha sonra
    guvenilir bicimde bulunabilir.
    """
    xpath = (
        "//div[normalize-space(text())='Show More' or "
        "normalize-space(text())='Daha Fazla Göster' or "
        "normalize-space(text())='Daha Fazla Goster']"
    )
    dugmeler = driver.find_elements(By.XPATH, xpath)
    tiklanan = 0

    for dugme in dugmeler:
        try:
            if not dugme.is_displayed():
                continue
            driver.execute_script(
                r"""
                const dugme = arguments[0];
                const tarih = /yay\u0131nland\u0131|yayinlandi|published|posted|konaklad|stayed/i;
                const puan = /(^|\s)(10(?:[.,]0)?|[0-9](?:[.,][0-9])?)(\s|\/10|$)/;
                let ust = dugme.parentElement;
                let yedek = null;
                for (let i = 0; i < 14 && ust; i++, ust = ust.parentElement) {
                    const yazi = (ust.innerText || '').trim();
                    const sinif = String(ust.className || '');
                    if (/review|comment/i.test(sinif)) yedek = ust;
                    if (yazi.length >= 25 && yazi.length <= 20000 &&
                        tarih.test(yazi) && puan.test(yazi)) {
                        ust.setAttribute('data-codex-review-card', 'true');
                        yedek = null;
                        break;
                    }
                }
                if (yedek) yedek.setAttribute('data-codex-review-card', 'true');
                dugme.setAttribute('data-codex-show-more', 'clicked');
                """,
                dugme,
            )
            # JavaScript tiklamasi sayfa yukarida olsa bile butonu acar ve
            # sabit baslik/cerez katmanlarindan etkilenmez.
            driver.execute_script("arguments[0].click();", dugme)
            tiklanan += 1
        except (JavascriptException, StaleElementReferenceException):
            continue

    if tiklanan:
        time.sleep(1)
    return tiklanan


def yorum_sayfasini_hazirla(driver: webdriver.Chrome, sayfa_no: int) -> None:
    """Bekle, sayfanin en altina in ve butun uzun yorumlari genislet."""
    time.sleep(YORUM_SAYFASI_BEKLEME)
    driver.execute_script(
        "const s=document.querySelector(\"div[class*='drawer_drawerContainer-content']\") "
        "|| document.scrollingElement || document.documentElement;"
        "s.scrollTop=s.scrollHeight;"
    )
    time.sleep(1)
    adet = show_more_butonlarini_ac(driver)
    print(f"{sayfa_no}. sayfa hazırlandı; {adet} Show More düğmesi açıldı.")


def otel_adini_al(driver: webdriver.Chrome) -> str:
    seciciler = (
        "h1[class*='hotelNameRow_hotelOverview_name']",
        "h1[aria-label][data-interactive='true']",
        "h1",
    )
    for secici in seciciler:
        try:
            oge = WebDriverWait(driver, BEKLEME).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, secici))
            )
            ad = temizle(oge.get_attribute("aria-label") or oge.text)
            if ad:
                return ad
        except TimeoutException:
            continue
    raise RuntimeError("Otel adi bulunamadi; sayfa tam yuklenmemis olabilir.")


def daha_fazla_yorumu_ac(driver: webdriver.Chrome) -> None:
    onceki_sekmeler = set(driver.window_handles)
    dugme = WebDriverWait(driver, BEKLEME).until(
        EC.element_to_be_clickable((By.ID, "review-swiper-show-more-button"))
    )
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", dugme)
    try:
        dugme.click()
    except Exception:
        driver.execute_script("arguments[0].click();", dugme)

    # Bazi Trip.com surumleri yorumlari yeni sekmede acar.
    try:
        WebDriverWait(driver, 5).until(
            lambda d: len(set(d.window_handles) - onceki_sekmeler) > 0
        )
        yeni_sekme = (set(driver.window_handles) - onceki_sekmeler).pop()
        driver.switch_to.window(yeni_sekme)
    except TimeoutException:
        pass

KART_SECICILERI = (
    "div[data-exposure*='htl_t_online_cmt_list_card_exposure']",
    "[data-codex-review-card='true']",
    "[data-testid*='review-card']",
    "[data-test*='review-card']",
    "div[class*='reviewListItem']",
    "div[class*='ReviewListItem']",
    "div[class*='reviewCard']",
    "div[class*='ReviewCard']",
    "div[class*='reviewItem']",
    "li[class*='reviewItem']",
    "div[class*='commentItem']",
)


def _kart_mi(oge: WebElement) -> bool:
    try:
        metin = temizle(oge.text)
        if len(metin) < 25 or len(metin) > 20_000:
            return False
        sayi_var = bool(re.search(r"(?<!\d)(?:10(?:[.,]0)?|[0-9](?:[.,]\d)?)(?!\d)", metin))
        tarih_var = bool(
            re.search(
                r"yayınlandı|yayinlandi|published|posted|konaklad|stayed|入住|发布",
                metin,
                re.I,
            )
        )
        return sayi_var and tarih_var
    except StaleElementReferenceException:
        return False


def yorum_kartlarini_bul(driver: webdriver.Chrome) -> list[WebElement]:
    """Rastgele CSS son eklerine ragmen yorum kartlarini bulur."""
    # Trip.com her gercek yorum kartina benzersiz writingid iceren bu exposure
    # degerini veriyor. Varsa yedek tahmin algoritmalarina hic girmemek gerekir;
    # aksi halde kartin yalnizca puan/tarih basligi secilebilir.
    kesin_kartlar = driver.find_elements(
        By.CSS_SELECTOR,
        "div[data-exposure*='htl_t_online_cmt_list_card_exposure']",
    )
    if kesin_kartlar:
        return [kart for kart in kesin_kartlar if _kart_mi(kart)]

    bulunan: list[WebElement] = []
    for secici in KART_SECICILERI:
        bulunan.extend(driver.find_elements(By.CSS_SELECTOR, secici))

    # Tum yorumlari kapsamak icin tarih yazan her yapraktan en yakin puanli
    # kapsayiciya cik. Bu yol Show More bulunmayan kisa yorumlari da yakalar.
    bulunan.extend(
        driver.execute_script(
            r"""
            const tarih = /yay\u0131nland\u0131|yayinlandi|published|posted|konaklad|stayed/i;
            const puan = /(^|\s)(10(?:[.,]0)?|[0-9](?:[.,][0-9])?)(\s|\/10|$)/;
            const sonuc = [];
            for (const alt of document.querySelectorAll('font,span,p')) {
              const kendi = (alt.innerText || '').trim();
              if (!kendi || kendi.length > 180 || !tarih.test(kendi)) continue;
              let ust = alt.parentElement;
              for (let i = 0; i < 14 && ust; i++, ust = ust.parentElement) {
                const yazi = (ust.innerText || '').trim();
                if (yazi.length >= 25 && yazi.length <= 20000 && puan.test(yazi)) {
                  ust.setAttribute('data-codex-review-card', 'true');
                  sonuc.push(ust);
                  break;
                }
              }
            }
            return [...new Set(sonuc)];
            """
        )
    )

    # Ic ice bulunan kartlardan ayni metne sahip en kucuk olani koru.
    benzersiz: dict[str, WebElement] = {}
    for kart in bulunan:
        if not _kart_mi(kart):
            continue
        try:
            anahtar = temizle(kart.text)
            mevcut = benzersiz.get(anahtar)
            if mevcut is None or kart.size["height"] < mevcut.size["height"]:
                benzersiz[anahtar] = kart
        except StaleElementReferenceException:
            continue
    adaylar = sorted(benzersiz.values(), key=lambda oge: len(temizle(oge.text)))
    en_icteki_kartlar: list[WebElement] = []
    for aday in adaylar:
        try:
            # Bir aday zaten kabul edilen daha kucuk bir karti kapsiyorsa bu,
            # yorum listesinin/kart dis cercevesinin kendisidir.
            if any(
                driver.execute_script("return arguments[0].contains(arguments[1]);", aday, ic)
                for ic in en_icteki_kartlar
            ):
                continue
            en_icteki_kartlar.append(aday)
        except StaleElementReferenceException:
            continue
    return en_icteki_kartlar


def _secici_metni(kart: WebElement, seciciler: Iterable[str]) -> str:
    for secici in seciciler:
        for oge in kart.find_elements(By.CSS_SELECTOR, secici):
            try:
                metin = temizle(oge.text)
                if metin:
                    return metin
            except StaleElementReferenceException:
                continue
    return ""


def _yaprak_metinler(kart: WebElement) -> list[str]:
    """Karttaki kullaniciya gorunen, kisa ve tekrarsiz metinleri dondurur."""
    sonuc: list[str] = []
    # Trip.com yorum metnini span/font yerine cocuksuz bir div icinde tutuyor.
    for oge in kart.find_elements(
        By.XPATH, ".//font | .//span | .//p | .//strong | .//div[not(*)]"
    ):
        try:
            if not oge.is_displayed():
                continue
            metin = temizle(oge.text)
            if not metin or metin in sonuc:
                continue
            # Ust oge ile ayni metni tasiyan gereksiz kapsayicilari azalt.
            cocuk_metinleri = [
                temizle(c.text) for c in oge.find_elements(By.XPATH, "./font|./span|./p")
            ]
            if metin in cocuk_metinleri:
                continue
            sonuc.append(metin)
        except StaleElementReferenceException:
            continue
    return sonuc


def _desene_gore(metinler: Iterable[str], desen: str) -> str:
    ifade = re.compile(desen, re.I)
    return next((m for m in metinler if ifade.search(m)), "")


def _ikon_satiri_metni(kart: WebElement, ikon_sinifi: str) -> str:
    """Profil listesindeki ikonun ait oldugu li satirinin metnini alir."""
    try:
        satir = kart.find_element(
            By.XPATH,
            f".//i[contains(concat(' ', normalize-space(@class), ' '), "
            f"' {ikon_sinifi} ')]/ancestor::li[1]",
        )
        return temizle(satir.text)
    except NoSuchElementException:
        return ""


def karti_oku(kart: WebElement, otel_adi: str) -> YorumKaydi | None:
    metinler = _yaprak_metinler(kart)
    if not metinler:
        return None

    yorum_tarihi = _secici_metni(
        kart,
        (
            "[class*='publishDate']",
            "[class*='PublishDate']",
            "[class*='reviewDate']",
            "[class*='ReviewDate']",
            "[class*='createTime']",
        ),
    ) or _desene_gore(metinler, r"yayınlandı|yayinlandi|published|posted|发布")

    konaklama_tarihi = _ikon_satiri_metni(
        kart, "u-icon-ic_new_calendar_line"
    ) or _secici_metni(
        kart,
        (
            "[class*='stayDate']",
            "[class*='StayDate']",
            "[class*='checkInDate']",
            "[class*='travelDate']",
        ),
    ) or _desene_gore(metinler, r"konaklad|stayed|入住")

    oda_tipi = _ikon_satiri_metni(kart, "ic_roomline")

    puan = _secici_metni(
        kart,
        (
            "strong",
            "[class*='reviewScore']",
            "[class*='ReviewScore']",
            "[class*='score']",
            "[class*='rating']",
        ),
    )
    puan_eslesme = re.search(
        r"(?<!\d)(10(?:[.,]0)?|[0-9](?:[.,]\d)?)(?:\s*/\s*10)?(?!\d)", puan
    )
    if not puan_eslesme:
        for metin in metinler:
            tam = re.fullmatch(r"\s*(10(?:[.,]0)?|[0-9](?:[.,]\d)?)(?:\s*/\s*10)?\s*", metin)
            if tam:
                puan_eslesme = tam
                break
    puan = puan_eslesme.group(1) if puan_eslesme else ""

    toplam_yorum = _ikon_satiri_metni(kart, "ic_message") or _secici_metni(
        kart,
        (
            "[class*='reviewCount']",
            "[class*='ReviewCount']",
            "[class*='commentCount']",
            "[class*='userComment']",
        ),
    ) or _desene_gore(metinler, r"\b\d+\s*(?:yorum|reviews?|点评|則評價)\b")

    seyahat_tipi = _ikon_satiri_metni(kart, "ic_business2") or _secici_metni(
        kart,
        (
            "[class*='tripType']",
            "[class*='TripType']",
            "[class*='travelType']",
            "[class*='TravelType']",
        ),
    ) or _desene_gore(
        metinler,
        r"^(?:aile|çift|cift|yalnız|yalniz|arkadaşlar|arkadaslar|iş|is|"
        r"family|couple|solo|friends|business|traveling with friends|"
        r"travelling with friends)(?:\s.*)?$",
    )

    kademe = _secici_metni(
        kart,
        (
            "[class*='memberLevel']",
            "[class*='MemberLevel']",
            "[class*='userLevel']",
            "[class*='UserLevel']",
            "[class*='membership']",
            "[class*='memberTag']",
        ),
    ) or _desene_gore(
        metinler,
        r"^(?:elmas|diamond|platin|platinum|altın|altin|gold|gümüş|gumus|silver)$",
    )

    konum = _ikon_satiri_metni(kart, "ic_languages") or _secici_metni(
        kart,
        (
            "[class*='reviewerLocation']",
            "[class*='ReviewerLocation']",
            "[class*='userLocation']",
            "[class*='UserLocation']",
            "[class*='country']",
            "[class*='Country']",
        ),
    )

    yorum = _secici_metni(
        kart,
        (
            ".UXjSnokalMIS5CzMtLSM",
            "[class*='reviewContent']",
            "[class*='ReviewContent']",
            "[class*='reviewText']",
            "[class*='ReviewText']",
            "[class*='commentContent']",
            "[class*='CommentContent']",
        ),
    )

    meta = {
        yorum_tarihi,
        konaklama_tarihi,
        oda_tipi,
        toplam_yorum,
        seyahat_tipi,
        kademe,
        konum,
        puan,
    }
    yorum_adaylari = [
        m
        for m in metinler
        if m not in meta
        and len(m) >= 8
        and not re.search(
            r"show more|daha fazla|original text|orijinal metin|translation|çeviri",
            m,
            re.I,
        )
    ]
    # DOM sirasinda yorum, "Posted ..." tarihinden hemen sonra gelir. En uzun
    # metni secmek tesis cevabini yorum sanabildigi icin ilk uygun metni aliriz.
    tarih_sonrasi: list[str] = []
    if yorum_tarihi in metinler:
        tarih_sonrasi = metinler[metinler.index(yorum_tarihi) + 1 :]
    ilk_yorum_adayi = next(
        (m for m in tarih_sonrasi if m in yorum_adaylari),
        yorum_adaylari[0] if yorum_adaylari else "",
    )
    if not yorum or len(yorum) < 20:
        yorum = ilk_yorum_adayi
    elif ilk_yorum_adayi and any(
        bilgi and bilgi in yorum
        for bilgi in (yorum_tarihi, konaklama_tarihi, toplam_yorum, seyahat_tipi)
    ):
        yorum = ilk_yorum_adayi

    # Konum sinifi degismisse: tarih ile konaklama tarihi arasindaki kisa metin
    # genellikle kullanicinin ulkesidir (ornegin "Azerbaycan").
    if not konum and yorum_tarihi in metinler:
        baslangic = metinler.index(yorum_tarihi) + 1
        bitis = metinler.index(konaklama_tarihi) if konaklama_tarihi in metinler else len(metinler)
        yasak = meta | {yorum}
        aradakiler = [
            m
            for m in metinler[baslangic:bitis]
            if m not in yasak and 2 <= len(m) <= 80 and not any(ch.isdigit() for ch in m)
        ]
        if aradakiler:
            konum = aradakiler[-1]

    if not yorum:
        return None
    return YorumKaydi(
        otel_adi=otel_adi,
        yorum=yorum,
        puan=puan,
        yorum_tarihi=yorum_tarihi,
        konum=konum,
        konaklama_tarihi=konaklama_tarihi,
        oda_tipi=oda_tipi,
        musteri_toplam_yorum_sayisi=toplam_yorum,
        musteri_kademe=kademe,
        seyahat_tipi=seyahat_tipi,
    )


def kayit_anahtari(kayit: YorumKaydi) -> tuple[str, ...]:
    return (
        temizle(kayit.otel_adi).casefold(),
        temizle(kayit.yorum).casefold(),
        temizle(kayit.yorum_tarihi).casefold(),
        temizle(kayit.puan).casefold(),
    )


def eski_kayitlari_oku(csv_dosyasi: Path) -> set[tuple[str, ...]]:
    anahtarlar: set[tuple[str, ...]] = set()
    if not csv_dosyasi.exists() or csv_dosyasi.stat().st_size == 0:
        return anahtarlar
    try:
        with csv_dosyasi.open("r", encoding="utf-8-sig", newline="") as akim:
            for satir in csv.DictReader(akim):
                kayit = YorumKaydi(
                    **{alan: satir.get(alan) or "" for alan in CSV_ALANLARI}
                )
                anahtarlar.add(kayit_anahtari(kayit))
    except (OSError, csv.Error, TypeError) as hata:
        print(f"Uyari: Eski CSV okunamadi ({hata}); yeni kayitlar yine yazilacak.")
    return anahtarlar


def csvyi_normalize_et(csv_dosyasi: Path) -> None:
    """Eksik alanlari bos birakip her satiri CSV_ALANLARI ile hizalar."""
    if not csv_dosyasi.exists() or csv_dosyasi.stat().st_size == 0:
        return

    with csv_dosyasi.open("r", encoding="utf-8-sig", newline="") as akim:
        okuyucu = csv.DictReader(akim)
        eski_alanlar = okuyucu.fieldnames or []
        satirlar = list(okuyucu)

    normalizasyon_gerekli = eski_alanlar != list(CSV_ALANLARI) or any(
        None in satir or any(satir.get(alan) is None for alan in CSV_ALANLARI)
        for satir in satirlar
    )
    if not normalizasyon_gerekli:
        return

    gecici_dosya = csv_dosyasi.with_suffix(csv_dosyasi.suffix + ".tmp")
    with gecici_dosya.open("w", encoding="utf-8-sig", newline="") as akim:
        yazici = csv.DictWriter(akim, fieldnames=CSV_ALANLARI)
        yazici.writeheader()
        for satir in satirlar:
            yazici.writerow({alan: satir.get(alan) or "" for alan in CSV_ALANLARI})
    gecici_dosya.replace(csv_dosyasi)


def sonraki_sayfa_dugmesi(driver: webdriver.Chrome) -> WebElement | None:
    xpathler = (
        "//li[.//i[contains(@class,'u-icon-arrowRight')]]/a",
        "//a[.//i[contains(@class,'arrowRight')]]",
        "//button[@aria-label='Next' or @aria-label='Sonraki']",
        "//a[@aria-label='Next' or @aria-label='Sonraki']",
    )
    for xpath in xpathler:
        for dugme in driver.find_elements(By.XPATH, xpath):
            try:
                kapsayici = dugme.find_element(By.XPATH, "..")
                durum = " ".join(
                    (
                        dugme.get_attribute("class") or "",
                        kapsayici.get_attribute("class") or "",
                        dugme.get_attribute("aria-disabled") or "",
                    )
                ).lower()
                if dugme.is_displayed() and "disabled" not in durum and "true" not in durum:
                    return dugme
            except (NoSuchElementException, StaleElementReferenceException):
                continue
    return None


def sayfa_imzasi(driver: webdriver.Chrome) -> str:
    kartlar = yorum_kartlarini_bul(driver)
    if not kartlar:
        return ""
    try:
        return temizle(kartlar[0].text)[:500]
    except StaleElementReferenceException:
        return ""


def yorumlari_cek(driver: webdriver.Chrome, url: str, csv_dosyasi: Path) -> int:
    driver.get(url)
    takvimi_kapat(driver)
    otel_adi = otel_adini_al(driver)
    print(f"Otel adı: {otel_adi}")
    daha_fazla_yorumu_ac(driver)

    csv_dosyasi.parent.mkdir(parents=True, exist_ok=True)
    csvyi_normalize_et(csv_dosyasi)
    mevcut_anahtarlar = eski_kayitlari_oku(csv_dosyasi)
    yeni_dosya = not csv_dosyasi.exists() or csv_dosyasi.stat().st_size == 0
    toplam = 0
    sayfa_no = 1
    gorulen_sayfalar: set[str] = set()

    with csv_dosyasi.open("a", encoding="utf-8-sig", newline="") as akim:
        yazici = csv.DictWriter(akim, fieldnames=CSV_ALANLARI)
        if yeni_dosya:
            yazici.writeheader()
            akim.flush()

        while True:
            # Her sayfada ayni sira: 2 sn bekle, en alta in, tum uzun
            # yorumlari ac, sonra kartlari okuyup CSV'ye kaydet.
            yorum_sayfasini_hazirla(driver, sayfa_no)
            try:
                WebDriverWait(driver, BEKLEME).until(lambda d: bool(yorum_kartlarini_bul(d)))
            except TimeoutException:
                raise RuntimeError(
                    "Yorum kartlari bulunamadi. Sayfa insan dogrulamasi gosteriyor "
                    "veya Trip.com sayfa yapisini degistirmis olabilir."
                )

            kartlar = yorum_kartlarini_bul(driver)
            print(f"{sayfa_no}. sayfada {len(kartlar)} yorum kartı bulundu.")
            try:
                imza = temizle(kartlar[0].text)[:500]
            except (IndexError, StaleElementReferenceException):
                imza = ""
            if imza and imza in gorulen_sayfalar:
                print("Ayni yorum sayfasi yeniden acildi; dongu sonlandiriliyor.")
                break
            gorulen_sayfalar.add(imza)

            sayfa_adedi = 0
            for kart in kartlar:
                try:
                    kayit = karti_oku(kart, otel_adi)
                except StaleElementReferenceException:
                    continue
                if kayit is None:
                    continue
                anahtar = kayit_anahtari(kayit)
                if anahtar in mevcut_anahtarlar:
                    continue
                satir = asdict(kayit)
                yazici.writerow({alan: satir.get(alan) or "" for alan in CSV_ALANLARI})
                akim.flush()  # Program kesilse bile cekilen yorumlar kaybolmasin.
                mevcut_anahtarlar.add(anahtar)
                toplam += 1
                sayfa_adedi += 1
            print(f"{sayfa_no}. sayfa: {sayfa_adedi} yeni yorum kaydedildi.")

            # Genisleyen yorumlar sayfa boyunu degistirebilir; sonraki sayfa
            # okunu aramadan once tekrar en alta in.
            driver.execute_script(
                "const s=document.querySelector(\"div[class*='drawer_drawerContainer-content']\") "
                "|| document.scrollingElement || document.documentElement;"
                "s.scrollTop=s.scrollHeight;"
            )
            time.sleep(1)
            dugme = sonraki_sayfa_dugmesi(driver)
            if dugme is None:
                break

            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", dugme)
            try:
                dugme.click()
            except Exception:
                driver.execute_script("arguments[0].click();", dugme)
            sayfa_no += 1

    print(f"Tamamlandı. Toplam çekilen yorum sayısı: {toplam}")
    return toplam


def argumanlari_al() -> argparse.Namespace:
    ayrıştırıcı = argparse.ArgumentParser(
        description="Trip.com otel yorumlarini trip_yorum.csv dosyasina kaydeder."
    )
    ayrıştırıcı.add_argument(
        "url",
        nargs="?",
        default=OTEL_URL,
        help="Trip.com otel URL'si (verilmezse dosyadaki OTEL_URL kullanilir).",
    )
    ayrıştırıcı.add_argument(
        "--csv",
        type=Path,
        default=CSV_DOSYASI,
        help="CSV cikti yolu (varsayilan: trip_yorum.csv).",
    )
    ayrıştırıcı.add_argument(
        "--headless",
        action="store_true",
        help="Chrome penceresini gostermeden calistir.",
    )
    return ayrıştırıcı.parse_args()


def main() -> int:
    ayarlar = argumanlari_al()
    driver: webdriver.Chrome | None = None
    try:
        driver = tarayici_olustur(ayarlar.headless)
        yorumlari_cek(driver, ayarlar.url, ayarlar.csv.resolve())
        return 0
    except KeyboardInterrupt:
        print("Kullanıcı tarafından durduruldu; yazılan kayıtlar CSV'de korundu.")
        return 130
    except Exception as hata:
        print(f"Hata: {hata}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
