import streamlit as st

# ---------- ALWAYS RENDER UI FIRST ----------
st.set_page_config(page_title="CSV Image Downloader", layout="wide")
st.title("📦 CSV Image Downloader")
st.write("Upload CSV → download images → ZIP export")

# ---------- SAFE IMPORTS ----------
try:
    import pandas as pd
    import requests
    import re
    import zipfile
    from io import BytesIO
    from urllib.parse import urlparse
    from PIL import Image, UnidentifiedImageError
except Exception as e:
    st.error("❌ Import error (very important):")
    st.exception(e)
    st.stop()

# ---------- CONFIG ----------
REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}


# ---------- HELPERS ----------
def safe_filename(name):
    return re.sub(r'[<>:"/\\|?*\n\r\t]', "_", str(name).strip())


def is_valid_url(url):
    try:
        parsed = urlparse(str(url))
        return parsed.scheme in ("http", "https") and parsed.netloc
    except:
        return False


# ⭐ BULLETPROOF IMAGE DOWNLOADER
def download_image_bytes(url):

    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()

        data = r.content

        try:
            img = Image.open(BytesIO(data))
            img.load()
        except UnidentifiedImageError:
            return None, "Not image"

        fmt = (img.format or "").lower()

        # ---- FORMAT HANDLING ----
        if fmt in ["jpeg", "jpg"]:
            ext = "jpg"

        elif fmt == "png":
            ext = "png"

        elif fmt == "webp":
            ext = "webp"

        elif fmt in ["tiff", "tif"]:
            img = img.convert("RGB")
            ext = "jpg"

        else:
            img = img.convert("RGB")
            ext = "jpg"

        if ext == "jpg" and img.mode != "RGB":
            img = img.convert("RGB")

        buffer = BytesIO()

        if ext == "jpg":
            img.save(buffer, "JPEG", quality=95)
        else:
            img.save(buffer, ext.upper())

        buffer.seek(0)

        return buffer.read(), ext

    except Exception as e:
        return None, str(e)


# ---------- APP ----------
try:

    uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

    if uploaded_file:

        df = pd.read_csv(uploaded_file)

        df = df.applymap(
            lambda x: str(x).strip().replace("\u200b", "")
            if isinstance(x, str)
            else x
        )

        product_col = None
        for col in df.columns:
            if any(k in col.lower() for k in ["name", "product", "title", "item"]):
                product_col = col
                break

        if product_col is None:
            product_col = df.columns[0]
            st.warning("Product column not detected — using first column.")

        st.success(f"Using product column: {product_col}")

        if st.button("🚀 Start Download"):

            progress = st.progress(0)
            status = st.empty()

            zip_buffer = BytesIO()

            total = 0
            success = 0
            errors = []

            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:

                rows = len(df)

                for i, (_, row) in enumerate(df.iterrows()):

                    product_name = safe_filename(row[product_col])
                    image_count = 0

                    for col in df.columns:

                        if col == product_col:
                            continue

                        url = row[col]

                        if pd.isnull(url) or not str(url).strip():
                            continue

                        if not is_valid_url(url):
                            continue

                        total += 1
                        status.text(f"Downloading {total}")

                        data, result = download_image_bytes(url)

                        if data:
                            name = (
                                f"{product_name}_{image_count}"
                                if image_count > 0
                                else product_name
                            )

                            zipf.writestr(f"{name}.{result}", data)

                            image_count += 1
                            success += 1
                        else:
                            errors.append(f"{url} → {result}")

                    progress.progress((i + 1) / rows)

            zip_buffer.seek(0)

            st.success("✅ Finished")

            c1, c2, c3 = st.columns(3)
            c1.metric("Found", total)
            c2.metric("Downloaded", success)
            c3.metric("Errors", len(errors))

            if errors:
                with st.expander("Errors"):
                    st.write(errors[:200])

            st.download_button(
                "⬇ Download ZIP",
                zip_buffer,
                "images.zip",
                "application/zip",
            )

except Exception as e:
    st.error("❌ Runtime error:")
    st.exception(e)
