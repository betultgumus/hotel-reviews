"""Google Travel otel yorumlarini Selenium ile CSV dosyasina kaydeder.

Kullanim:
    python yorum.py
    python yorum.py "https://www.google.com/travel/search?..."

Not: Google'in sayfa yapisi zamanla degisebilir. Bu betik, kullanicinin verdigi
CSS siniflarini ve bunlara ek olarak daha genel DOM kontrollerini kullanir.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    JavascriptException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# Bu adresi daha sonra istediginiz Google Travel adresiyle degistirebilirsiniz.
GOOGLE_TRAVEL_URL = (
    "https://www.google.com/travel/search?gsas=1&ts=EggKAggDCgIIAxocEhoSFAoHCOoPEAk"
    "YCxIHCOoPEAkYDBgBMgIIAg&qs=MhRDZ3NJeEtQeXI2enE0WlRlQVJBQjgC&ap=KigK"
    "EgnFcjtd4JNCQBFbV4iejI07QBISCRNtVSmRlEJAEVtXiBKQjjtAugEHcmV2aWV3cw"
    "&ictx=111&rlz=1C5GCEM_enTR1207TR1208&biw=1393&bih=730&hl=tr-TR"
    "&ved=0CAAQ5JsGahcKEwiYv8rbv7uWAxUAAAAAHQAAAAAQAw"
)

# url değiştirilirse kaydedilecek .csv adı da değiştirilmelidir
CSV_DOSYASI = Path(__file__).with_name("yorum.csv")
SAYFA_ACILIS_BEKLEMESI = 3
KAYDIRMA_BEKLEMESI = 2
SONDA_BOS_KAYDIRMA_LIMITI = 3
VARSAYILAN_MAKSIMUM_YORUM = 0  # 0: sinir yok

CSV_ALANLARI = ("otel_adi", "yorum", "hizmet", "tarih", "puan")


@dataclass(frozen=True)
class YorumKaydi:
    otel_adi: str
    yorum: str
    hizmet: str
    tarih: str
    puan: str


def metni_temizle(metin: str) -> str:
    """Satir sonlarini koruyup gereksiz bosluklari temizler."""
    satirlar = []
    for satir in metin.replace("\r", "\n").split("\n"):
        temiz = re.sub(r"[ \t\f\v]+", " ", satir).strip()
        if temiz and (not satirlar or temiz != satirlar[-1]):
            satirlar.append(temiz)
    return "\n".join(satirlar).strip()


def kayit_anahtari(kayit: YorumKaydi) -> str:
    # Hizmet/tarih sonradan yuklenmese bile ayni yorum ikinci kez yazilmasin.
    icerik = f"{kayit.otel_adi}\0{kayit.yorum}"
    return hashlib.sha256(icerik.encode("utf-8")).hexdigest()


class AnindaCsvYazici:
    """Her kaydi ekledikten sonra dosyayi hemen diske yazar."""

    def __init__(self, dosya: Path) -> None:
        self.dosya = dosya
        self.dosya.parent.mkdir(parents=True, exist_ok=True)
        self._satirlar: list[dict[str, str]] = []
        self._anahtar_indeksi: dict[str, int] = {}
        self._sema_guncellenmeli = False
        self.mevcut_anahtarlar = self._mevcut_kayitlari_oku()
        dosya_yeni = not self.dosya.exists() or self.dosya.stat().st_size == 0
        if self._sema_guncellenmeli:
            self._csvyi_yeniden_yaz(acik_akimi_yeniden_acma=False)
        self._akimi_ac()
        if dosya_yeni:
            self._yazici.writeheader()
            self._diske_yaz()

    def _akimi_ac(self) -> None:
        self._akim = self.dosya.open("a", newline="", encoding="utf-8-sig")
        self._yazici = csv.DictWriter(self._akim, fieldnames=CSV_ALANLARI)

    def _mevcut_kayitlari_oku(self) -> set[str]:
        if not self.dosya.exists() or self.dosya.stat().st_size == 0:
            return set()

        anahtarlar: set[str] = set()
        try:
            with self.dosya.open("r", newline="", encoding="utf-8-sig") as akim:
                okuyucu = csv.DictReader(akim)
                self._sema_guncellenmeli = okuyucu.fieldnames != list(CSV_ALANLARI)
                for satir in okuyucu:
                    normal_satir = {
                        "otel_adi": satir.get("otel_adi", ""),
                        "yorum": satir.get("yorum", ""),
                        "hizmet": satir.get("hizmet", ""),
                        "tarih": satir.get("tarih", "") or "",
                        "puan": satir.get("puan", "") or "",
                    }
                    kayit = YorumKaydi(
                        normal_satir["otel_adi"],
                        normal_satir["yorum"],
                        normal_satir["hizmet"],
                        normal_satir["tarih"],
                        normal_satir["puan"],
                    )
                    anahtar = kayit_anahtari(kayit)
                    if anahtar not in anahtarlar:
                        self._anahtar_indeksi[anahtar] = len(self._satirlar)
                        self._satirlar.append(normal_satir)
                        anahtarlar.add(anahtar)
        except (OSError, csv.Error) as hata:
            print(f"Uyari: Eski CSV okunamadi: {hata}", file=sys.stderr)
        return anahtarlar

    def _csvyi_yeniden_yaz(self, acik_akimi_yeniden_acma: bool = True) -> None:
        """Mevcut satirlari kaybetmeden semayi veya tarihi gunceller."""
        if acik_akimi_yeniden_acma:
            self._akim.close()
        gecici = self.dosya.with_name(f"{self.dosya.name}.tmp")
        with gecici.open("w", newline="", encoding="utf-8-sig") as akim:
            yazici = csv.DictWriter(akim, fieldnames=CSV_ALANLARI)
            yazici.writeheader()
            yazici.writerows(self._satirlar)
            akim.flush()
            os.fsync(akim.fileno())
        os.replace(gecici, self.dosya)
        if acik_akimi_yeniden_acma:
            self._akimi_ac()

    def _diske_yaz(self) -> None:
        self._akim.flush()
        os.fsync(self._akim.fileno())

    def ekle(self, kayit: YorumKaydi) -> str:
        """eklendi, guncellendi veya ayni sonucunu dondurur."""
        anahtar = kayit_anahtari(kayit)
        if anahtar in self.mevcut_anahtarlar:
            indeks = self._anahtar_indeksi[anahtar]
            guncellendi = False
            if kayit.tarih and not self._satirlar[indeks].get("tarih"):
                self._satirlar[indeks]["tarih"] = kayit.tarih
                guncellendi = True
            if kayit.puan and not self._satirlar[indeks].get("puan"):
                self._satirlar[indeks]["puan"] = kayit.puan
                guncellendi = True
            if guncellendi:
                self._csvyi_yeniden_yaz()
                return "guncellendi"
            return "ayni"

        satir = {
            "otel_adi": kayit.otel_adi,
            "yorum": kayit.yorum,
            "hizmet": kayit.hizmet,
            "tarih": kayit.tarih,
            "puan": kayit.puan,
        }
        self._yazici.writerow(satir)
        self._diske_yaz()
        self._anahtar_indeksi[anahtar] = len(self._satirlar)
        self._satirlar.append(satir)
        self.mevcut_anahtarlar.add(anahtar)
        return "eklendi"

    def kapat(self) -> None:
        self._akim.close()

    def __enter__(self) -> "AnindaCsvYazici":
        return self

    def __exit__(self, *_: object) -> None:
        self.kapat()


def tarayici_olustur(headless: bool) -> webdriver.Chrome:
    secenekler = webdriver.ChromeOptions()
    secenekler.add_argument("--lang=tr-TR")
    secenekler.add_argument("--disable-notifications")
    secenekler.add_argument("--disable-blink-features=AutomationControlled")
    secenekler.add_argument("--window-size=1400,900")
    secenekler.add_experimental_option(
        "prefs", {"intl.accept_languages": "tr-TR,tr,en-US,en"}
    )
    if headless:
        secenekler.add_argument("--headless=new")
    # Selenium Manager uygun ChromeDriver'i otomatik bulur/indirir.
    return webdriver.Chrome(options=secenekler)


def otel_adini_al(driver: webdriver.Chrome) -> str:
    seciciler = (
        (By.CSS_SELECTOR, "h1.FNkAEc.o4k8l"),
        (By.CSS_SELECTOR, "h1[jsname='Xmv8Ce']"),
        (By.CSS_SELECTOR, "h1[role='heading']"),
    )
    for nasil, secici in seciciler:
        try:
            eleman = WebDriverWait(driver, 12).until(
                EC.visibility_of_element_located((nasil, secici))
            )
            metin = metni_temizle(eleman.text)
            if metin:
                return metin
        except TimeoutException:
            continue
    return "Bilinmeyen otel"


def devamini_ac(driver: webdriver.Chrome) -> int:
    """Ekrandaki yorumlara ait tum Devami/More dugmelerini acar."""
    xpath = (
        "//span[@role='button' and "
        "(normalize-space()='Devamı' or normalize-space()='Devami' "
        "or normalize-space()='More') ]"
    )
    acilan = 0
    try:
        dugmeler = driver.find_elements(By.XPATH, xpath)
    except Exception:
        return 0

    for dugme in dugmeler:
        try:
            if not dugme.is_displayed():
                continue
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center', inline:'nearest'});", dugme
            )
            try:
                dugme.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", dugme)
            acilan += 1
        except (JavascriptException, StaleElementReferenceException):
            continue
    return acilan


def yorum_kartlarini_bul(driver: webdriver.Chrome) -> list[WebElement]:
    """Ayrinti blogundan en yakin yorum kartini bulur."""
    ayrintilar = driver.find_elements(By.CSS_SELECTOR, "div.X4nL7d")
    kartlar: list[WebElement] = []
    gorulen: set[str] = set()

    for ayrinti in ayrintilar:
        try:
            kart = driver.execute_script(
                """
                const detail = arguments[0];
                let node = detail;
                let fallback = detail.parentElement;
                while (node && node !== document.body) {
                    const text = (node.innerText || '').trim();
                    const hasLongText = Array.from(node.querySelectorAll('span'))
                        .some(s => (s.innerText || '').trim().length >= 80);
                    if (hasLongText && text.length >= 100 && text.length <= 12000) {
                        fallback = node;
                    }
                    if (node.matches('[data-review-id], [jsdata*="review"]')) {
                        return node;
                    }
                    node = node.parentElement;
                }
                return fallback;
                """,
                ayrinti,
            )
            if not kart:
                continue
            kimlik = kart.id
            if kimlik not in gorulen:
                gorulen.add(kimlik)
                kartlar.append(kart)
        except (JavascriptException, StaleElementReferenceException):
            continue
    return kartlar


def yorum_metnini_al(kart: WebElement, ayrinti_metni: str) -> str:
    """Karttaki en uzun, ayrinti blogundan farkli span metnini yorum kabul eder."""
    adaylar: list[str] = []
    try:
        spanlar = kart.find_elements(By.CSS_SELECTOR, "span")
    except StaleElementReferenceException:
        return ""

    for span in spanlar:
        try:
            if span.find_elements(By.XPATH, "ancestor::div[contains(@class,'X4nL7d')]"):
                continue
            metin = metni_temizle(span.text)
            if (
                len(metin) >= 40
                and metin != ayrinti_metni
                and metin not in {"Devamı", "Devami", "More"}
            ):
                adaylar.append(metin)
        except StaleElementReferenceException:
            continue

    if not adaylar:
        return ""

    # Ayni yorum DOM'da iki kez bulunabiliyor; en uzun olan yeterlidir.
    return max(set(adaylar), key=len)


def karttan_kayit_al(kart: WebElement, otel_adi: str) -> YorumKaydi | None:
    try:
        ayrinti = kart.find_element(By.CSS_SELECTOR, "div.X4nL7d")
        hizmet = metni_temizle(ayrinti.text)
        yorum = yorum_metnini_al(kart, hizmet)
    except (NoSuchElementException, StaleElementReferenceException):
        return None

    if not yorum:
        return None
    return YorumKaydi(
        otel_adi=otel_adi,
        yorum=yorum,
        hizmet=hizmet,
        tarih="",
        puan="",
    )


def yorum_listesi_imzasi(driver: webdriver.Chrome) -> str:
    """Yuklenen yorumlar degistiginde degisen kisa bir DOM imzasi uretir."""
    try:
        icerik = driver.execute_script(
            """
            return Array.from(document.querySelectorAll('.X4nL7d'))
                .map(detail => {
                    let card = detail;
                    for (let i = 0; i < 6 && card.parentElement; i++) {
                        card = card.parentElement;
                    }
                    return (card.innerText || detail.innerText || '').slice(-5000);
                })
                .join('|---YORUM---|');
            """
        )
    except JavascriptException:
        return ""
    return hashlib.sha256(str(icerik).encode("utf-8")).hexdigest()


def sayfayi_asagi_kaydir(driver: webdriver.Chrome) -> bool:
    """Son yorumdan hareketle dogru yorum panelini en alta kaydirir."""
    try:
        return bool(
            driver.execute_script(
                """
                const details = Array.from(document.querySelectorAll('.X4nL7d'));
                const lastDetail = details[details.length - 1];
                let target = lastDetail ? lastDetail.parentElement : null;

                while (target && target !== document.body) {
                    const style = getComputedStyle(target);
                    const scrollable = /(auto|scroll)/.test(style.overflowY) &&
                                       target.scrollHeight > target.clientHeight + 50;
                    if (scrollable) break;
                    target = target.parentElement;
                }

                if (target) {
                    const before = target.scrollTop;
                    target.scrollTo({top: target.scrollHeight, behavior: 'auto'});
                    target.dispatchEvent(new Event('scroll', {bubbles: true}));
                    return target.scrollTop > before;
                }

                const before = window.scrollY;
                window.scrollTo({top: document.documentElement.scrollHeight,
                                 behavior: 'auto'});
                window.dispatchEvent(new Event('scroll', {bubbles: true}));
                return window.scrollY > before;
                """
            )
        )
    except (JavascriptException, StaleElementReferenceException):
        return False


def yeni_yorumlari_bekle(driver: webdriver.Chrome, onceki_imza: str) -> bool:
    """Kaydirma sonrasinda yeni yorum DOM'a eklenene kadar bekler."""
    try:
        WebDriverWait(
            driver,
            YENI_YORUM_YUKLEME_SURESI,
            poll_frequency=0.5,
        ).until(lambda tarayici: yorum_listesi_imzasi(tarayici) != onceki_imza)
        time.sleep(KAYDIRMA_BEKLEMESI)
        return True
    except TimeoutException:
        return False


def devamini_ac_guvenli(driver: webdriver.Chrome) -> int:
    """Yalnizca yorum kartlarindaki Devami dugmelerini acar."""
    acilan = 0
    try:
        dugmeler = driver.find_elements(
            By.CSS_SELECTOR, "span.Jmi7d.TJUuge[role='button']"
        )
    except Exception:
        return 0

    izinli_metinler = {"Devam\u0131", "Devami", "More"}
    for dugme in dugmeler:
        try:
            if not dugme.is_displayed() or dugme.text.strip() not in izinli_metinler:
                continue
            try:
                dugme.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", dugme)
            acilan += 1
        except (JavascriptException, StaleElementReferenceException):
            continue
    return acilan


def yorum_kartlarini_bul_guvenli(driver: webdriver.Chrome) -> list[WebElement]:
    """Hizmet veya tarih alanindan ait olduklari yorum kartlarini bulur."""
    anchors = driver.find_elements(By.CSS_SELECTOR, "div.X4nL7d, span.iUtr1.CQYfx")
    kartlar: list[WebElement] = []
    gorulen: set[str] = set()

    for anchor in anchors:
        try:
            kart = driver.execute_script(
                """
                const anchor = arguments[0];
                let node = anchor;
                let fallback = null;
                while (node && node !== document.body) {
                    if (node.matches('[data-review-id], [jsdata*="review"]')) {
                        return node;
                    }
                    const text = (node.innerText || '').trim();
                    const hasDate = node.querySelector('.iUtr1.CQYfx');
                    const hasLongText = Array.from(node.querySelectorAll('span'))
                        .some(s => (s.innerText || '').trim().length >= 40);
                    if (!fallback && hasDate && hasLongText &&
                        text.length >= 60 && text.length <= 12000) {
                        fallback = node;
                    }
                    node = node.parentElement;
                }
                return fallback || anchor.parentElement;
                """,
                anchor,
            )
            if kart and kart.id not in gorulen:
                gorulen.add(kart.id)
                kartlar.append(kart)
        except (JavascriptException, StaleElementReferenceException):
            continue
    return kartlar


def karttan_kayit_al_guvenli(
    kart: WebElement, otel_adi: str
) -> YorumKaydi | None:
    try:
        ayrintilar = kart.find_elements(By.CSS_SELECTOR, "div.X4nL7d")
        hizmet = metni_temizle(ayrintilar[0].text) if ayrintilar else ""
        tarihler = kart.find_elements(By.CSS_SELECTOR, "span.iUtr1.CQYfx")
        tarih = metni_temizle(tarihler[0].text) if tarihler else ""
        puanlar = kart.find_elements(By.CSS_SELECTOR, "div.GDWaad")
        puan = metni_temizle(puanlar[0].text) if puanlar else ""
        yorum = yorum_metnini_al(kart, hizmet)
    except StaleElementReferenceException:
        return None

    if not yorum:
        return None
    return YorumKaydi(otel_adi, yorum, hizmet, tarih, puan)


def sayfayi_artimli_kaydir(driver: webdriver.Chrome) -> bool:
    """Once biraz yukari, ardindan bir ekran asagi kaydirir."""
    try:
        hedef = driver.execute_script(
            """
                const nodes = Array.from(document.querySelectorAll('div'));
                const candidates = nodes.filter(el => {
                    const style = getComputedStyle(el);
                    return /(auto|scroll)/.test(style.overflowY) &&
                           el.scrollHeight > el.clientHeight + 50 &&
                           (el.querySelector('.X4nL7d') ||
                            el.querySelector('.iUtr1.CQYfx'));
                });
                const target = candidates.sort(
                    (a, b) => (b.scrollHeight - b.clientHeight) -
                              (a.scrollHeight - a.clientHeight)
                )[0];
                return target || null;
            """
        )

        if hedef:
            onceki_konum = int(driver.execute_script("return arguments[0].scrollTop;", hedef))
            driver.execute_script(
                """
                const target = arguments[0];
                target.scrollTop = Math.max(target.scrollTop - 250, 0);
                target.dispatchEvent(new Event('scroll', {bubbles: true}));
                """,
                hedef,
            )
            time.sleep(0.4)
            driver.execute_script(
                """
                const target = arguments[0];
                const step = Math.max(target.clientHeight * 0.85, 500) + 250;
                target.scrollTop = Math.min(target.scrollTop + step,
                                            target.scrollHeight);
                target.dispatchEvent(new Event('scroll', {bubbles: true}));
                """,
                hedef,
            )
            yeni_konum = int(
                driver.execute_script("return arguments[0].scrollTop;", hedef)
            )
            return yeni_konum > onceki_konum

        onceki_konum = int(driver.execute_script("return window.scrollY;"))
        driver.execute_script(
            "window.scrollBy(0, -250); "
            "window.dispatchEvent(new Event('scroll', {bubbles: true}));"
        )
        time.sleep(0.4)
        driver.execute_script(
            "window.scrollBy(0, Math.max(window.innerHeight * 0.85, 600) + 250); "
            "window.dispatchEvent(new Event('scroll', {bubbles: true}));"
        )
        yeni_konum = int(driver.execute_script("return window.scrollY;"))
        return yeni_konum > onceki_konum
    except (JavascriptException, StaleElementReferenceException):
        return False


def yorumlari_cek(
    driver: webdriver.Chrome,
    url: str,
    csv_dosyasi: Path,
    maksimum_yorum: int,
) -> int:
    driver.get(url)
    time.sleep(SAYFA_ACILIS_BEKLEMESI)
    otel_adi = otel_adini_al(driver)
    print(f"Otel: {otel_adi}")

    toplam = 0
    oturumda_gorulen: set[str] = set()
    sonda_bos_kaydirma = 0
    with AnindaCsvYazici(csv_dosyasi) as yazici:
        while True:
            devamini_ac_guvenli(driver)
            turde_yeni_kart = 0
            for kart in yorum_kartlarini_bul_guvenli(driver):
                kayit = karttan_kayit_al_guvenli(kart, otel_adi)
                if not kayit:
                    continue

                anahtar = kayit_anahtari(kayit)
                if anahtar not in oturumda_gorulen:
                    oturumda_gorulen.add(anahtar)
                    turde_yeni_kart += 1

                sonuc = yazici.ekle(kayit)
                if sonuc == "eklendi":
                    toplam += 1
                    print(f"[{toplam}] Yorum CSV'ye kaydedildi.")
                    if maksimum_yorum > 0 and toplam >= maksimum_yorum:
                        return toplam
            print("Gorunen yorumlar tarandi; yeni yorumlar icin asagi kaydiriliyor...")
            hareket_etti = sayfayi_artimli_kaydir(driver)
            time.sleep(KAYDIRMA_BEKLEMESI)

            if turde_yeni_kart > 0 or hareket_etti:
                sonda_bos_kaydirma = 0
            else:
                sonda_bos_kaydirma += 1

            if sonda_bos_kaydirma >= SONDA_BOS_KAYDIRMA_LIMITI:
                print("Son kaydirmalarda yeni yorum acilmadi; liste tamamlandi.")
                break
                print("Son kaydırmada yeni yorum açılmadı; listenin sonuna gelindi.")
                break

    return toplam


def argumanlari_oku() -> argparse.Namespace:
    cozumleyici = argparse.ArgumentParser(
        description="Google Travel otel yorumlarini yorum.csv dosyasina kaydeder."
    )
    cozumleyici.add_argument(
        "url",
        nargs="?",
        default=GOOGLE_TRAVEL_URL,
        help="Taranacak Google Travel URL'si.",
    )
    cozumleyici.add_argument(
        "--csv",
        type=Path,
        default=CSV_DOSYASI,
        help=f"CSV cikti yolu (varsayilan: {CSV_DOSYASI.name}).",
    )
    cozumleyici.add_argument(
        "--maksimum-yorum",
        type=int,
        default=VARSAYILAN_MAKSIMUM_YORUM,
        help="0 sinirsizdir; pozitif degerde bu sayida yeni kayittan sonra durur.",
    )
    cozumleyici.add_argument(
        "--headless",
        action="store_true",
        help="Chrome penceresini gostermeden calistirir.",
    )
    return cozumleyici.parse_args()


def main() -> int:
    ayarlar = argumanlari_oku()
    if ayarlar.maksimum_yorum < 0:
        print("Hata: --maksimum-yorum negatif olamaz.", file=sys.stderr)
        return 2

    driver: webdriver.Chrome | None = None
    try:
        driver = tarayici_olustur(ayarlar.headless)
        adet = yorumlari_cek(
            driver,
            ayarlar.url,
            ayarlar.csv.resolve(),
            ayarlar.maksimum_yorum,
        )
        print(f"Tamamlandı. Bu çalışmada {adet} yeni yorum kaydedildi.")
        return 0
    except KeyboardInterrupt:
        print("Kullanici tarafindan durduruldu. Kaydedilen yorumlar CSV'de korundu.")
        return 130
    except Exception as hata:
        print(f"Hata: {hata}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
