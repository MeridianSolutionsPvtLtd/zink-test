from dotenv import load_dotenv
load_dotenv(".env", override=True)
from azure.storage.blob import BlobServiceClient
import os

cc = BlobServiceClient.from_connection_string(
    os.environ["AZURE_STORAGE_CONNECTION_STRING"]
).get_container_client("zinc-data")
for b in cc.list_blobs(name_starts_with="output/"):
    mb = (b.size or 0) / (1024 * 1024)
    print(f"{b.name}\t{mb:.2f} MB")
