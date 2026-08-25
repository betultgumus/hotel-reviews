"""TripAdvisor otel yorumlarini Selenium ile CSV dosyasina kaydeder.

Kullanim:
    python tripadvisor_yorum.py
    python tripadvisor_yorum.py "https://www.tripadvisor.com.tr/..."
    python tripadvisor_yorum.py --headless

Baska bir oteli cekmek icin OTEL_URL degiskenini degistirmek yeterlidir.
Her yorum sayfasi okunduktan sonra yeni kayitlar CSV'ye eklenip diske yazilir.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
import unicodedata
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
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# Baska bir otel icin yalnizca bu degeri degistirebilirsiniz.
OTEL_URL = (
    "https://www.tripadvisor.com.tr/Hotel_Review-g298658-d2062371-Reviews-"
    "Green_Bay_Resort_Spa-Bodrum_City_Bodrum_District_Mugla_Province_"
    "Turkish_Aegean_Coast.html#REVIEWS"
)
CSV_DOSYASI = Path(__file__).with_name("tripadvisor_yorum.csv")

SAYFA_ACILIS_BEKLEMESI = 2
ALTA_INDIKTEN_SONRA_BEKLEME = 1
WEB_DRIVER_BEKLEMESI = 25

CSV_ALANLARI = (
    "otel_adi",
    "yorum",
    "yorum_basligi",
    "puan",
    "yorum_tarihi",
    "konum",
    "konaklama_tarihi",
    "seyahat_turu",
    "value_rating",
    "rooms_rating",
    "location_rating",
    "cleanliness_rating",
    "service_rating",
    "musteri_toplam_yorum_sayisi",
)

KATEGORI_KOLONLARI = {
    "deger": "value_rating",
    "odalar": "rooms_rating",
    "yer": "location_rating",
    "temizlik": "cleanliness_rating",
    "hizmet": "service_rating",
}


@dataclass(frozen=True)
class YorumKaydi:
    otel_adi: str
    yorum: str
    yorum_basligi: str
    puan: str
    yorum_tarihi: str
    konum: str
    konaklama_tarihi: str
    seyahat_turu: str
    value_rating: str
    rooms_rating: str
    location_rating: str
    cleanliness_rating: str
    service_rating: str
    musteri_toplam_yorum_sayisi: str


def temizle(metin: str | None) -> str:
    return re.sub(r"\s+", " ", metin or "").strip()


def anahtara_cevir(metin: str | None) -> str:
    """Turkce karakter ve buyuk/kucuk harf farkini kaldirir."""
    metin = temizle(metin).casefold().replace("ı", "i")
    return "".join(
        karakter
        for karakter in unicodedata.normalize("NFKD", metin)
        if not unicodedata.combining(karakter)
    )


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

    driver = webdriver.Chrome(options=secenekler)
    driver.set_page_load_timeout(60)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": (
                "Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});"
            )
        },
    )
    return driver


def otel_adini_al(driver: webdriver.Chrome) -> str:
    for secici in ("h1#HEADING", "h1[data-automation='hotelName']", "h1"):
        try:
            oge = WebDriverWait(driver, WEB_DRIVER_BEKLEMESI).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, secici))
            )
            otel_adi = temizle(oge.text)
            if otel_adi:
                return otel_adi
        except TimeoutException:
            continue
    raise RuntimeError("Otel adi bulunamadi; sayfa yuklenmemis olabilir.")


def sayfanin_altina_in(driver: webdriver.Chrome) -> None:
    driver.execute_script(
        "window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'});"
    )


def devamini_oku_butonlarini_ac(driver: webdriver.Chrome) -> int:
    """Gorunen butun 'Devamini okuyun' dugmelerini, kart kart acar."""
    xpath = (
        "//button[.//span[normalize-space()='Devamını okuyun' or "
        "normalize-space()='Devamini okuyun' or "
        "normalize-space()='Read more']]"
    )
    dugmeler = driver.find_elements(By.XPATH, xpath)
    tiklanan = 0
    for dugme in dugmeler:
        try:
            if not dugme.is_displayed() or not dugme.is_enabled():
                continue
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", dugme)
            try:
                dugme.click()
            except Exception:
                driver.execute_script("arguments[0].click();", dugme)
            tiklanan += 1
        except (JavascriptException, StaleElementReferenceException):
            continue
    if tiklanan:
        time.sleep(0.5)
    return tiklanan


def yorum_sayfasini_hazirla(driver: webdriver.Chrome, sayfa_no: int) -> None:
    time.sleep(SAYFA_ACILIS_BEKLEMESI)
    sayfanin_altina_in(driver)
    time.sleep(ALTA_INDIKTEN_SONRA_BEKLEME)
    tiklanan = devamini_oku_butonlarini_ac(driver)
    print(f"{sayfa_no}. sayfa hazirlandi; {tiklanan} 'Devamini okuyun' dugmesi acildi.")


def yorum_kartlarini_bul(driver: webdriver.Chrome) -> list[WebElement]:
    """Once semantik TripAdvisor secicilerini, sonra guvenli yedekleri dener."""
    for secici in (
        "div[data-automation='reviewCard']",
        "article[data-automation='reviewCard']",
        "[data-test-target='HR_CC_CARD']",
    ):
        kartlar = driver.find_elements(By.CSS_SELECTOR, secici)
        if kartlar:
            return kartlar

    # Son care: yorum basligindan, kendisini ve puan SVG'sini kapsayan en yakin
    # makul kapsayiciya cik. Bulunan elemanlar DOM'da tekrar isaretlenir.
    return driver.execute_script(
        r"""
        const sonuc = [];
        for (const baslik of document.querySelectorAll('[data-test-target="review-title"]')) {
          let ust = baslik;
          for (let i = 0; i < 10 && ust; i++, ust = ust.parentElement) {
            const yazi = (ust.innerText || '').trim();
            if (yazi.length > 30 && yazi.length < 30000 &&
                ust.querySelector('svg[data-automation="bubbleRatingImage"]')) {
              ust.setAttribute('data-codex-review-card', 'true');
              sonuc.push(ust);
              break;
            }
          }
        }
        return [...new Set(sonuc)];
        """
    )


def ilk_metin(kok: WebElement, seciciler: Iterable[str]) -> str:
    for secici in seciciler:
        for oge in kok.find_elements(By.CSS_SELECTOR, secici):
            try:
                metin = temizle(oge.text)
                if metin:
                    return metin
            except StaleElementReferenceException:
                continue
    return ""


def en_uzun_metin(kok: WebElement, seciciler: Iterable[str]) -> str:
    adaylar: list[str] = []
    for secici in seciciler:
        for oge in kok.find_elements(By.CSS_SELECTOR, secici):
            try:
                metin = temizle(oge.text)
                if metin:
                    adaylar.append(metin)
            except StaleElementReferenceException:
                continue
        if adaylar:
            break
    return max(adaylar, key=len, default="")


def svg_puanini_al(svg: WebElement) -> str:
    """Oncelikle title'i okur; yoksa dolu baloncuk path'lerini sayar."""
    try:
        title = temizle(svg.find_element(By.TAG_NAME, "title").get_attribute("textContent"))
        eslesme = re.search(
            r"(?:uzerinden|üzerinden|out of)\s*(?:\u00a0|\s)*(\d+(?:[.,]\d+)?)",
            title,
            re.I,
        )
        if eslesme:
            return eslesme.group(1).replace(",", ".")
    except NoSuchElementException:
        pass

    # Verilen HTML'de dolu path yalniz dis daireyi, bos path ise ek olarak
    # "zm0 2a..." ic daire komutunu tasiyor.
    dolu = 0
    for path in svg.find_elements(By.TAG_NAME, "path"):
        d = (path.get_attribute("d") or "").replace(" ", "").lower()
        if d and "zm02a" not in d:
            dolu += 1
    return str(dolu) if dolu else ""


def dolu_balon_sayisini_al(svg: WebElement) -> str:
    """Genel puani, ornekte istendigi gibi dolu SVG path sayisindan alir."""
    dolu = 0
    for path in svg.find_elements(By.TAG_NAME, "path"):
        d = (path.get_attribute("d") or "").replace(" ", "").lower()
        if d and "zm02a" not in d:
            dolu += 1
    return str(dolu) if dolu else ""


def genel_puani_al(kart: WebElement) -> str:
    svgler = kart.find_elements(
        By.CSS_SELECTOR, "svg[data-automation='bubbleRatingImage']"
    )
    for svg in svgler:
        try:
            # Kategori SVG'lerini genel puan sanma.
            kategori_ici = svg.find_elements(
                By.XPATH,
                "./ancestor::div[contains(concat(' ',normalize-space(@class),' '),' yifGl ')][1]",
            )
            if kategori_ici:
                continue
            puan = dolu_balon_sayisini_al(svg) or svg_puanini_al(svg)
            if puan:
                return puan
        except StaleElementReferenceException:
            continue
    return svg_puanini_al(svgler[0]) if svgler else ""


def kategori_puanlarini_al(kart: WebElement) -> dict[str, str]:
    """Kategori sirasina degil, kategori metni ile yanindaki SVG'ye bakar."""
    sonuc = {kolon: "" for kolon in KATEGORI_KOLONLARI.values()}
    etiketler = kart.find_elements(
        By.XPATH,
        ".//*[self::div or self::span][normalize-space()='Değer' or "
        "normalize-space()='Deger' or normalize-space()='Odalar' or "
        "normalize-space()='Yer' or normalize-space()='Temizlik' or "
        "normalize-space()='Hizmet']",
    )
    for etiket in etiketler:
        try:
            kolon = KATEGORI_KOLONLARI.get(anahtara_cevir(etiket.text))
            if not kolon:
                continue
            # Reverse ancestor eksenindeki [1], SVG iceren en yakin satirdir.
            satir = etiket.find_element(
                By.XPATH,
                "./ancestor-or-self::div[.//svg[@data-automation='bubbleRatingImage']][1]",
            )
            svg = satir.find_element(
                By.CSS_SELECTOR, "svg[data-automation='bubbleRatingImage']"
            )
            sonuc[kolon] = svg_puanini_al(svg)
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return sonuc


AYLAR = (
    "oca|sub|şub|mar|nis|may|haz|tem|agu|ağu|eyl|eki|kas|ara|"
    "ocak|subat|şubat|mart|nisan|mayis|mayıs|haziran|temmuz|"
    "agustos|ağustos|eylul|eylül|ekim|kasim|kasım|aralik|aralık|"
    "jan|feb|apr|jun|jul|aug|sep|oct|nov|dec"
)


def yorum_tarihini_al(kart: WebElement) -> str:
    adaylar = kart.find_elements(By.CSS_SELECTOR, "div.biGQs._P.VImYz.AWdfh")
    for oge in adaylar:
        metin = temizle(oge.text)
        if re.search(r"yorumunu\s+yazdi|yorumunu\s+yazdı|wrote a review", metin, re.I):
            # "Kullanici, Tem 2026 yorumunu yazdi" -> "Tem 2026"
            eslesme = re.search(
                rf"((?:{AYLAR})[a-zçğıöşü]*\s+\d{{4}})", metin, re.I
            )
            return eslesme.group(1) if eslesme else metin
    return ilk_metin(
        kart,
        (
            "[data-automation='reviewDate']",
            "[data-test-target='review-date']",
            "div[class*='reviewDate']",
        ),
    )


def profil_alanlarini_al(kart: WebElement) -> tuple[str, str]:
    """Konum ile kullanicinin toplam katki/yorum sayisini ayirir."""
    konum = ""
    toplam_yorum = ""
    for oge in kart.find_elements(By.CSS_SELECTOR, "span.biGQs._P.VImYz.AWdfh"):
        metin = temizle(oge.text)
        if not metin:
            continue
        if re.search(r"\b\d[\d.,]*\s*(?:katki|katkı|contribution|review)", metin, re.I):
            toplam_yorum = metin
        elif not konum and not re.search(r"\d", metin):
            konum = metin
    return konum, toplam_yorum


def konaklama_ve_seyahat_turunu_al(kart: WebElement) -> tuple[str, str]:
    konaklama = ""
    seyahat_turu = ""
    for oge in kart.find_elements(By.CSS_SELECTOR, "span.biGQs._P.VImYz.xENVe"):
        metin = temizle(oge.text)
        if not metin:
            continue
        if re.search(rf"\b(?:{AYLAR})[a-zçğıöşü]*\s+\d{{4}}\b", metin, re.I):
            konaklama = metin
        elif re.search(
            r"seyahat|aile|cift|çift|yalniz|yalnız|arkadas|arkadaş|is icin|iş için|"
            r"traveled|travelled|family|couple|solo|friends|business",
            metin,
            re.I,
        ):
            seyahat_turu = metin
    return konaklama, seyahat_turu


def yorum_basligini_al(kart: WebElement) -> str:
    baslik = ilk_metin(
        kart,
        (
            "[data-test-target='review-title']",
            "[data-automation='reviewTitle']",
        ),
    )
    if baslik:
        return baslik

    # Bu sinif hem yorum basliginda hem kullanici adinda gorulebiliyor.
    # Profil baglantisinin icindeki span'i baslik olarak kabul etme.
    for oge in kart.find_elements(By.CSS_SELECTOR, "span.biGQs._P.SewaP.OgHoE"):
        try:
            if oge.find_elements(By.XPATH, "./ancestor::a[contains(@href,'/Profile/')]"):
                continue
            metin = temizle(oge.text)
            if metin:
                return metin
        except StaleElementReferenceException:
            continue
    return ""


def karti_oku(kart: WebElement, otel_adi: str) -> YorumKaydi | None:
    yorum_basligi = yorum_basligini_al(kart)
    yorum = en_uzun_metin(
        kart,
        (
            "[data-test-target='review-body'] span",
            "[data-automation='reviewText'] span",
            "[data-automation='reviewText']",
            "div[class*='reviewText'] span",
            "q span",
        ),
    )

    # Son yedek: karttaki uzun span'lardan baslik/meta olmayan en uzunu.
    if not yorum:
        yasak = {temizle(yorum_basligi)}
        adaylar = []
        for oge in kart.find_elements(By.TAG_NAME, "span"):
            metin = temizle(oge.text)
            if len(metin) >= 40 and metin not in yasak:
                adaylar.append(metin)
        yorum = max(adaylar, key=len, default="")
    if not yorum:
        return None

    kategori = kategori_puanlarini_al(kart)
    konum, toplam_yorum = profil_alanlarini_al(kart)
    konaklama, seyahat_turu = konaklama_ve_seyahat_turunu_al(kart)
    return YorumKaydi(
        otel_adi=otel_adi,
        yorum=yorum,
        yorum_basligi=yorum_basligi,
        puan=genel_puani_al(kart),
        yorum_tarihi=yorum_tarihini_al(kart),
        konum=konum,
        konaklama_tarihi=konaklama,
        seyahat_turu=seyahat_turu,
        value_rating=kategori["value_rating"],
        rooms_rating=kategori["rooms_rating"],
        location_rating=kategori["location_rating"],
        cleanliness_rating=kategori["cleanliness_rating"],
        service_rating=kategori["service_rating"],
        musteri_toplam_yorum_sayisi=toplam_yorum,
    )


def kayit_anahtari(kayit: YorumKaydi) -> tuple[str, ...]:
    return (
        anahtara_cevir(kayit.otel_adi),
        anahtara_cevir(kayit.yorum_basligi),
        anahtara_cevir(kayit.yorum),
        anahtara_cevir(kayit.yorum_tarihi),
    )


def csvyi_normalize_et(csv_dosyasi: Path) -> None:
    """Eski CSV varsa yeni kolonlari ekler; eski verileri kaybetmez."""
    if not csv_dosyasi.exists() or csv_dosyasi.stat().st_size == 0:
        return
    with csv_dosyasi.open("r", encoding="utf-8-sig", newline="") as akim:
        okuyucu = csv.DictReader(akim)
        eski_alanlar = okuyucu.fieldnames or []
        satirlar = list(okuyucu)
    if eski_alanlar == list(CSV_ALANLARI):
        return

    gecici = csv_dosyasi.with_suffix(csv_dosyasi.suffix + ".tmp")
    with gecici.open("w", encoding="utf-8-sig", newline="") as akim:
        yazici = csv.DictWriter(akim, fieldnames=CSV_ALANLARI)
        yazici.writeheader()
        for satir in satirlar:
            yazici.writerow({alan: satir.get(alan) or "" for alan in CSV_ALANLARI})
        akim.flush()
        os.fsync(akim.fileno())
    gecici.replace(csv_dosyasi)


def mevcut_anahtarlari_oku(csv_dosyasi: Path) -> set[tuple[str, ...]]:
    if not csv_dosyasi.exists() or csv_dosyasi.stat().st_size == 0:
        return set()
    sonuc: set[tuple[str, ...]] = set()
    with csv_dosyasi.open("r", encoding="utf-8-sig", newline="") as akim:
        for satir in csv.DictReader(akim):
            kayit = YorumKaydi(**{alan: satir.get(alan) or "" for alan in CSV_ALANLARI})
            sonuc.add(kayit_anahtari(kayit))
    return sonuc


def sayfayi_csvye_kaydet(
    csv_dosyasi: Path,
    kayitlar: Iterable[YorumKaydi],
    mevcut_anahtarlar: set[tuple[str, ...]],
) -> int:
    """Bir sayfanin tum yeni satirlarini ekler ve fiziksel olarak diske yazar."""
    yeni_dosya = not csv_dosyasi.exists() or csv_dosyasi.stat().st_size == 0
    kaydedilen = 0
    with csv_dosyasi.open("a", encoding="utf-8-sig", newline="") as akim:
        yazici = csv.DictWriter(akim, fieldnames=CSV_ALANLARI)
        if yeni_dosya:
            yazici.writeheader()
        for kayit in kayitlar:
            anahtar = kayit_anahtari(kayit)
            if anahtar in mevcut_anahtarlar:
                continue
            yazici.writerow(asdict(kayit))
            mevcut_anahtarlar.add(anahtar)
            kaydedilen += 1
        akim.flush()
        os.fsync(akim.fileno())
    return kaydedilen


def sonraki_sayfa_dugmesi(driver: webdriver.Chrome) -> WebElement | None:
    seciciler = (
        "a[data-smoke-attr='pagination-next-arrow']",
        "a[aria-label='Bir sonraki sayfa']",
        "a[aria-label='Next page']",
    )
    for secici in seciciler:
        for dugme in driver.find_elements(By.CSS_SELECTOR, secici):
            try:
                sinif = (dugme.get_attribute("class") or "").casefold()
                devre_disi = (dugme.get_attribute("aria-disabled") or "").casefold()
                href = dugme.get_attribute("href")
                if dugme.is_displayed() and href and devre_disi != "true" and "disabled" not in sinif:
                    return dugme
            except StaleElementReferenceException:
                continue
    return None


def ilk_kart_imzasi(driver: webdriver.Chrome) -> str:
    kartlar = yorum_kartlarini_bul(driver)
    if not kartlar:
        return ""
    try:
        return temizle(kartlar[0].text)[:500]
    except StaleElementReferenceException:
        return ""


def sonraki_sayfaya_gec(driver: webdriver.Chrome, dugme: WebElement) -> None:
    eski_imza = ilk_kart_imzasi(driver)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", dugme)
    try:
        dugme.click()
    except Exception:
        driver.execute_script("arguments[0].click();", dugme)
    WebDriverWait(driver, WEB_DRIVER_BEKLEMESI).until(
        lambda d: bool(ilk_kart_imzasi(d)) and ilk_kart_imzasi(d) != eski_imza
    )


def yorumlari_cek(driver: webdriver.Chrome, url: str, csv_dosyasi: Path) -> tuple[int, int]:
    driver.get(url)
    time.sleep(SAYFA_ACILIS_BEKLEMESI)
    otel_adi = otel_adini_al(driver)
    print(f"Otel adi: {otel_adi}")

    csv_dosyasi.parent.mkdir(parents=True, exist_ok=True)
    csvyi_normalize_et(csv_dosyasi)
    mevcut_anahtarlar = mevcut_anahtarlari_oku(csv_dosyasi)
    toplam_cekilen = 0
    toplam_kaydedilen = 0
    sayfa_no = 1
    gorulen_imzalar: set[str] = set()

    while True:
        yorum_sayfasini_hazirla(driver, sayfa_no)
        try:
            WebDriverWait(driver, WEB_DRIVER_BEKLEMESI).until(
                lambda d: bool(yorum_kartlarini_bul(d))
            )
        except TimeoutException as hata:
            raise RuntimeError(
                "Yorum kartlari bulunamadi. TripAdvisor dogrulama/cerez ekrani "
                "gosteriyor veya sayfa yapisi degismis olabilir."
            ) from hata

        kartlar = yorum_kartlarini_bul(driver)
        imza = ilk_kart_imzasi(driver)
        if imza and imza in gorulen_imzalar:
            print("Ayni yorum sayfasi tekrar acildi; dongu durduruldu.")
            break
        if imza:
            gorulen_imzalar.add(imza)

        sayfa_kayitlari: list[YorumKaydi] = []
        for kart in kartlar:
            try:
                kayit = karti_oku(kart, otel_adi)
                if kayit is not None:
                    sayfa_kayitlari.append(kayit)
            except StaleElementReferenceException:
                continue

        toplam_cekilen += len(sayfa_kayitlari)
        kaydedilen = sayfayi_csvye_kaydet(
            csv_dosyasi, sayfa_kayitlari, mevcut_anahtarlar
        )
        toplam_kaydedilen += kaydedilen
        print(
            f"{sayfa_no}. sayfa: {len(sayfa_kayitlari)} yorum cekildi, "
            f"{kaydedilen} yeni yorum CSV'ye kaydedildi."
        )

        sayfanin_altina_in(driver)
        time.sleep(ALTA_INDIKTEN_SONRA_BEKLEME)
        dugme = sonraki_sayfa_dugmesi(driver)
        if dugme is None:
            print("Bir sonraki sayfa butonu bulunamadi; islem sonlandiriliyor.")
            break
        try:
            sonraki_sayfaya_gec(driver, dugme)
        except TimeoutException:
            print("Sonraki sayfa yuklenmedi; islem sonlandiriliyor.")
            break
        sayfa_no += 1

    print(
        f"Tamamlandi. Toplam cekilen yorum: {toplam_cekilen}; "
        f"CSV'ye yeni kaydedilen yorum: {toplam_kaydedilen}."
    )
    return toplam_cekilen, toplam_kaydedilen


def argumanlari_al() -> argparse.Namespace:
    ayristirici = argparse.ArgumentParser(
        description="TripAdvisor yorumlarini tripadvisor_yorum.csv dosyasina kaydeder."
    )
    ayristirici.add_argument(
        "url",
        nargs="?",
        default=OTEL_URL,
        help="Otel URL'si (verilmezse dosyadaki OTEL_URL kullanilir).",
    )
    ayristirici.add_argument(
        "--csv",
        type=Path,
        default=CSV_DOSYASI,
        help="CSV cikti yolu (varsayilan: tripadvisor_yorum.csv).",
    )
    ayristirici.add_argument(
        "--headless",
        action="store_true",
        help="Chrome penceresini gostermeden calistir.",
    )
    return ayristirici.parse_args()


def main() -> int:
    ayarlar = argumanlari_al()
    driver: webdriver.Chrome | None = None
    try:
        driver = tarayici_olustur(ayarlar.headless)
        yorumlari_cek(driver, ayarlar.url, ayarlar.csv.resolve())
        return 0
    except KeyboardInterrupt:
        print("Kullanici durdurdu; tamamlanan sayfalar CSV'de korunuyor.")
        return 130
    except Exception as hata:
        print(f"Hata: {hata}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
