import re
from typing import List
from urllib.parse import urlparse
import re
from typing import Set

URL_REGEX = re.compile(r"https?://[^\s]+")


def extract_urls(text: str) -> List[str]:
    """Витягуємо всі URL з тексту й трохи чистимо від сміття типу ( ) , ."""
    if not text:
        return []

    raw = URL_REGEX.findall(text)
    cleaned: List[str] = []
    for u in raw:
        u = u.strip("()[],.<>\"'")
        if u:
            cleaned.append(u)
    return cleaned


def detect_brand(url: str) -> str | None:
    """
    Повертає 'zara', 'bershka' або None.
    """
    try:
        netloc = urlparse(url).netloc.lower()
    except Exception:
        return None

    if "zara.com" in netloc:
        return "zara"
    if "bershka.com" in netloc:
        return "bershka"
    return None


def extract_available_sizes(status_text: str) -> Set[str]:
    """
    З тексту статусу дістаємо множину розмірів, які зараз в наявності.
    Орієнтуємось на рядки типу:
      "🟢 M"
      "🟡 M"
      "• 🟢 В наявності: <b>M</b>"
    🟢 — є в наявності
    🟡 — залишилось кілька одиниць (також вважаємо «в наявності»)
    """
    sizes: Set[str] = set()

    for raw_line in status_text.splitlines():
        line = raw_line.strip()

        # якщо в рядку немає НІ 🟢, НІ 🟡 — пропускаємо
        if "🟢" not in line and "🟡" not in line:
            continue

        # прибираємо буліти типу "• "
        line = line.lstrip("• ").strip()

        # відрізаємо все до емодзі
        if "🟢" in line:
            line = line.split("🟢", 1)[1].strip()
        elif "🟡" in line:
            line = line.split("🟡", 1)[1].strip()

        # прибираємо HTML-теги
        line = re.sub(r"<.*?>", "", line).strip()

        if not line:
            continue

        # перше «слово» — це розмір: "M", "L", "XL", "42", ...
        size_token = line.split()[0].upper()
        sizes.add(size_token)

    return sizes