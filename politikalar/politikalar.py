"""Trip.com otel politikalarini Selenium ile CSV dosyasina kaydeder.

Kullanim:
    python politikalar.py
    python politikalar.py "https://www.trip.com/hotels/detail?hotelid=..."
    python politikalar.py --headless
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


OTEL_URL = (
    "https://www.trip.com/hotels/detail?hotelid=3451898&Allianceid=810504"
    "&Sid=1394411&utm_medium=cpc&utm_campaign=HPA&utm_source=google#review"
)
CSV_DOSYASI = Path(__file__).with_name("politikalar.csv")
BEKLEME = 30

CSV_ALANLARI = (
    "otel_adi",
    "giris_saati",
    "cıkıs_saati",
    "cocuk_politikası",
    "bebek_ve_ek_yatak",
    "kahvalti",
    "evcil_hayvan",
    "hizmet_hayvanları",
    "yas_sarti",
    "sertifika_numarasi",
    "hizmetler",
)


def temizle(metin: str | None) -> str:
    return re.sub(r"\s+", " ", metin or "").strip()


def tarayici_olustur(headless: bool) -> webdriver.Chrome:
    secenekler = webdriver.ChromeOptions()
    secenekler.add_argument("--lang=en-US")
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


def arama_butonuna_bas(driver: webdriver.Chrome) -> None:
    """Takvimi kapatmak icin ust arama cubugundaki Search dugmesine basar."""
    xpath = (
        "//button[contains(@class,'tripui-online-btn') "
        "and .//span[normalize-space()='Search']]"
    )
    dugme = gorunur_oge_bekle(driver, By.XPATH, xpath)
    try:
        dugme.click()
    except Exception:
        driver.execute_script("arguments[0].click();", dugme)
    print("Search dugmesine basildi.")
    time.sleep(2)


def gorunur_oge_bekle(
    driver: webdriver.Chrome, by: str, secici: str, sure: int = BEKLEME
) -> WebElement:
    """Ayni ogeden gizli kopyalar varsa ekranda gorunen kopyayi dondurur."""

    def gorunur_oge(d: webdriver.Chrome) -> WebElement | bool:
        for oge in d.find_elements(by, secici):
            try:
                if oge.is_displayed() and oge.size["width"] > 0 and oge.size["height"] > 0:
                    return oge
            except Exception:
                continue
        return False

    return WebDriverWait(driver, sure).until(gorunur_oge)


def otel_adini_al(driver: webdriver.Chrome) -> str:
    seciciler = (
        "h1[class*='hotelNameRow_hotelOverview_name'][aria-label]",
        "h1[data-interactive='true'][aria-label]",
        "h1[aria-label]",
    )
    for secici in seciciler:
        try:
            oge = gorunur_oge_bekle(driver, By.CSS_SELECTOR, secici, 10)
            otel_adi = temizle(oge.get_attribute("aria-label") or oge.text)
            if otel_adi:
                return otel_adi
        except TimeoutException:
            continue
    raise TimeoutException("Otel adi bulunamadi.")


def bes_kademe_asagi_in(driver: webdriver.Chrome) -> None:
    """Politika sekmesi yuklenmeden once bes gercek kaydirma hareketi yapar."""
    for adim in range(1, 6):
        onceki_konum = driver.execute_script("return window.scrollY;")
        try:
            ActionChains(driver).scroll_by_amount(0, 500).perform()
        except Exception:
            driver.execute_script("window.scrollBy({top:500,behavior:'auto'});")

        # Tembel yuklenen bolumlerin her tekerlek hareketini islemesine izin ver.
        try:
            WebDriverWait(driver, 3).until(
                lambda d: d.execute_script("return window.scrollY;") != onceki_konum
                or d.execute_script(
                    "return window.scrollY + innerHeight >= "
                    "document.documentElement.scrollHeight - 2;"
                )
            )
        except TimeoutException:
            # Sayfa tekerlek olayini yutsa bile scrollingElement'i ilerlet.
            driver.execute_script(
                "const s=document.scrollingElement||document.documentElement;"
                "s.scrollTop+=500;"
                "window.dispatchEvent(new Event('scroll'));"
            )
        print(f"Politikalar icin asagi kaydirma: {adim}/5")
        time.sleep(0.8)

    time.sleep(1)


def hizmetleri_al(driver: webdriver.Chrome) -> str:
    """Populer tesis olanaklarini sayfadaki aria-label degerlerinden alir."""
    secici = (
        "div[class*='hotelFacilityNew_hotelFacility-popular_list'] "
        "div[role='text'][aria-label]"
    )
    WebDriverWait(driver, BEKLEME).until(
        lambda d: bool(d.find_elements(By.CSS_SELECTOR, secici))
    )

    hizmetler: list[str] = []
    for oge in driver.find_elements(By.CSS_SELECTOR, secici):
        hizmet = temizle(oge.get_attribute("aria-label") or oge.text)
        if hizmet and hizmet not in hizmetler:
            hizmetler.append(hizmet)

    if not hizmetler:
        raise TimeoutException("Hizmetler listesi bulunamadi.")
    print(f"Hizmet sayisi: {len(hizmetler)}")
    return "; ".join(hizmetler)


def politika_icerigi_yuklendi(driver: webdriver.Chrome) -> bool:
    xpath = (
        "//strong[starts-with(normalize-space(.),'After') or "
        "starts-with(normalize-space(.),'Before')] | "
        "//span[starts-with(normalize-space(.),'Pets are') or "
        "starts-with(normalize-space(.),'Children of all ages')]"
    )
    return bool(driver.find_elements(By.XPATH, xpath))


def politikalar_sekmesini_ac(driver: webdriver.Chrome) -> None:
    # Masaustu ve mobil gezinme cubuklari DOM'da ayni anda bulunabiliyor.
    # Locator ile ilk esleseni almak gizli mobil kopyaya tiklanmasina yol
    # acabiliyordu; burada her denemede gorunen sekme yeniden bulunur.
    xpath = (
        "//h2[@role='tab' and "
        "(@aria-label='Policies' or normalize-space(.)='Policies')]"
    )
    son_hata: Exception | None = None

    for deneme in range(1, 5):
        try:
            sekme = gorunur_oge_bekle(driver, By.XPATH, xpath, 12)
            driver.execute_script(
                "arguments[0].scrollIntoView({block:'center',inline:'nearest'});"
                "window.scrollBy(0,-100);",
                sekme,
            )
            time.sleep(0.7)

            if deneme == 1:
                try:
                    ActionChains(driver).move_to_element(sekme).pause(0.3).click().perform()
                except Exception:
                    driver.execute_script("arguments[0].click();", sekme)
            else:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new MouseEvent('mousedown',"
                    "{bubbles:true}));"
                    "arguments[0].dispatchEvent(new MouseEvent('mouseup',"
                    "{bubbles:true}));"
                    "arguments[0].click();",
                    sekme,
                )

            WebDriverWait(driver, 6).until(
                lambda d: politika_icerigi_yuklendi(d)
                or any(
                    oge.get_attribute("aria-selected") == "true"
                    for oge in d.find_elements(By.XPATH, xpath)
                )
            )
            # Sekme secildikten sonra politika verilerinin gelmesini ayrica
            # bekle; aria-selected tek basina verinin yuklendigi anlamina gelmez.
            WebDriverWait(driver, 12).until(politika_icerigi_yuklendi)
            return
        except Exception as hata:
            son_hata = hata
            driver.execute_script("window.scrollBy(0, Math.max(250, innerHeight/3));")
            time.sleep(1)

    raise TimeoutException(
        "Gorunen Policies sekmesine tiklandi ancak politika icerigi yuklenmedi."
    ) from son_hata


def ilk_metin(driver: webdriver.Chrome, xpath: str) -> str:
    oge = WebDriverWait(driver, BEKLEME).until(
        EC.visibility_of_element_located((By.XPATH, xpath))
    )
    return temizle(oge.text)


def en_yakin_sag_kutu(oge: WebElement) -> WebElement:
    return oge.find_element(
        By.XPATH,
        "./ancestor::div[contains(@class,'hotelPolicyNew_hotelPolicy-item_right')][1]",
    )


def sag_kutu_metni(driver: webdriver.Chrome, baslangic: str) -> str:
    oge = WebDriverWait(driver, BEKLEME).until(
        EC.visibility_of_element_located(
            (
                By.XPATH,
                "//span[contains(@class,'hotelPolicyNew_hotelPolicy-item_description') "
                f"and starts-with(normalize-space(.), {xpath_dizesi(baslangic)})]",
            )
        )
    )
    return temizle(en_yakin_sag_kutu(oge).text)


def xpath_dizesi(deger: str) -> str:
    """Bir metni guvenli XPath dize ifadesine donusturur."""
    if "'" not in deger:
        return f"'{deger}'"
    if '"' not in deger:
        return f'"{deger}"'
    parcalar = deger.split("'")
    return "concat(" + ", \"'\", ".join(f"'{parca}'" for parca in parcalar) + ")"


def politikalari_oku(
    driver: webdriver.Chrome, otel_adi: str, hizmetler: str
) -> dict[str, str]:
    aciklama_sinifi = "hotelPolicyNew_hotelPolicy-item_description"

    giris = ilk_metin(
        driver,
        "//strong[contains(@class,'hotelPolicyNew_hotelPolicy-check_desc') "
        "and starts-with(normalize-space(.),'After')]",
    )
    cikis = ilk_metin(
        driver,
        "//strong[contains(@class,'hotelPolicyNew_hotelPolicy-check_desc') "
        "and starts-with(normalize-space(.),'Before')]",
    )
    bebek = ilk_metin(
        driver,
        f"//span[contains(@class,'{aciklama_sinifi}')]/strong["
        "starts-with(normalize-space(.),'For all room types')]",
    )
    evcil_hayvan = ilk_metin(
        driver,
        f"//span[contains(@class,'{aciklama_sinifi}') and "
        "starts-with(normalize-space(.),'Pets are')]",
    )
    hizmet_hayvanlari = ilk_metin(
        driver,
        f"//span[contains(@class,'{aciklama_sinifi}') and "
        "starts-with(normalize-space(.),'Service animals are')]",
    )
    yas_sarti = ilk_metin(
        driver,
        f"//span[contains(@class,'{aciklama_sinifi}') and "
        "contains(normalize-space(.),'must be at least')]",
    )
    sertifika = ilk_metin(
        driver,
        f"//span[contains(@class,'{aciklama_sinifi}') and "
        "starts-with(normalize-space(.),'License number:')]",
    )

    return {
        "otel_adi": otel_adi,
        "giris_saati": giris,
        "cıkıs_saati": cikis,
        "cocuk_politikası": sag_kutu_metni(driver, "Children of all ages"),
        "bebek_ve_ek_yatak": bebek,
        "kahvalti": sag_kutu_metni(driver, "Cuisine:"),
        "evcil_hayvan": evcil_hayvan,
        "hizmet_hayvanları": hizmet_hayvanlari,
        "yas_sarti": yas_sarti,
        "sertifika_numarasi": sertifika,
        "hizmetler": hizmetler,
    }


def csvye_yaz(kayit: dict[str, str], csv_dosyasi: Path) -> None:
    eksik_alanlar = [alan for alan in CSV_ALANLARI if not temizle(kayit.get(alan))]
    if eksik_alanlar:
        raise RuntimeError(
            "Su politika alanlari bulunamadi: " + ", ".join(eksik_alanlar)
        )

    csv_dosyasi.parent.mkdir(parents=True, exist_ok=True)
    with csv_dosyasi.open("w", encoding="utf-8-sig", newline="") as akim:
        yazici = csv.DictWriter(akim, fieldnames=CSV_ALANLARI)
        yazici.writeheader()
        yazici.writerow({alan: kayit.get(alan, "") for alan in CSV_ALANLARI})


def calistir(driver: webdriver.Chrome, url: str, csv_dosyasi: Path) -> None:
    driver.get(url)
    arama_butonuna_bas(driver)
    otel_adi = otel_adini_al(driver)
    print(f"Otel adi: {otel_adi}")
    bes_kademe_asagi_in(driver)
    hizmetler = hizmetleri_al(driver)
    politikalar_sekmesini_ac(driver)
    kayit = politikalari_oku(driver, otel_adi, hizmetler)
    csvye_yaz(kayit, csv_dosyasi)
    print(f"Politikalar kaydedildi: {csv_dosyasi}")


def argumanlari_al() -> argparse.Namespace:
    ayristirici = argparse.ArgumentParser(
        description="Trip.com otel politikalarini politikalar.csv dosyasina kaydeder."
    )
    ayristirici.add_argument("url", nargs="?", default=OTEL_URL)
    ayristirici.add_argument("--csv", type=Path, default=CSV_DOSYASI)
    ayristirici.add_argument("--headless", action="store_true")
    return ayristirici.parse_args()


def main() -> int:
    ayarlar = argumanlari_al()
    driver: webdriver.Chrome | None = None
    try:
        driver = tarayici_olustur(ayarlar.headless)
        calistir(driver, ayarlar.url, ayarlar.csv.resolve())
        return 0
    except KeyboardInterrupt:
        print("Kullanici tarafindan durduruldu.")
        return 130
    except TimeoutException as hata:
        ayrinti = temizle(str(hata).split("Stacktrace:", 1)[0])
        print(
            "Hata: "
            + (
                ayrinti
                or "Politikalar bolumu zamaninda yuklenmedi. Trip.com insan "
                "dogrulamasi gosteriyor olabilir."
            ),
            file=sys.stderr,
        )
        return 1
    except Exception as hata:
        print(f"Hata: {hata}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
