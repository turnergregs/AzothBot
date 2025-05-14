import os
import re
from supabase_client import supabase


def generate_image_filename(name: str, version: int) -> str:
	safe_name = re.sub(r'\W+', '_', name.lower()).strip('_')
	return f"{safe_name}_{version}.png"


def generate_local_filename(name: str) -> str:
	safe_name = re.sub(r'\W+', '_', name.lower()).strip('_')
	return f"{safe_name}.png"


def download_image(image_name: str, bucket: str, download_dir: str = "assets/downloaded_images") -> tuple[bool, str]:
	"""
	Downloads an image like 'test_new_9.png' and saves it with a timestamped filename.
	"""
	os.makedirs(download_dir, exist_ok=True)

	base_name = os.path.splitext(image_name)[0]
	local_name = generate_local_filename(base_name)
	local_path = os.path.join(download_dir, local_name)

	try:
		if os.path.exists(local_path):
			os.remove(local_path)
		data = supabase.storage.from_(bucket).download(image_name)
		with open(local_path, "wb") as f:
			f.write(data)
		return True, local_path

	except Exception as e:
		return False, f"Failed to download image: {e}"


def upload_image(name: str, image_bytes: bytes, bucket: str) -> tuple[bool, str]:
	"""
	Uploads an image using a flat name like 'new_card.png', overwriting any existing file.
	"""
	safe_name = re.sub(r"\W+", "_", name.lower()).strip("_")
	file_name = f"{safe_name}.png"

	try:
		upload_response = supabase.storage.from_(bucket).upload(
			file_name,
			image_bytes,
			{"content-type": "image/png", "x-upsert": "true"}
		)

		if hasattr(upload_response, "status_code") and upload_response.status_code >= 400:
			return False, f"Upload failed: {upload_response.text}"

		return True, file_name

	except Exception as e:
		return False, f"Exception during upload: {e}"
