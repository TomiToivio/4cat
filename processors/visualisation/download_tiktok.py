"""
Refresh a TikTok datasource
"""
import asyncio
import datetime
import json
from io import BytesIO
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from urllib.parse import urlparse, parse_qs
import requests

import common.config_manager as config
from common.lib.exceptions import ProcessorException
from common.lib.user_input import UserInput
from datasources.tiktok_urls.search_tiktok_urls import TikTokScraper
from backend.abstract.processor import BasicProcessor


__author__ = "Dale Wahl"
__credits__ = ["Dale Wahl"]
__maintainer__ = "Dale Wahl"
__email__ = "4cat@oilab.eu"


class TikTokVideoDownloader(BasicProcessor):
    type = "video-downloader-tiktok"  # job type ID
    category = "Visual"  # category
    title = "Download TikTok Videos"  # title displayed in UI
    description = "Downloads full videos for TikTok"
    extension = "zip"

    options = {
        "amount": {
            "type": UserInput.OPTION_TEXT,
            "help": "No. of videos (max 1000)",
            "default": 100,
            "min": 0,
            "max": 1000
        },
    }

    @classmethod
    def get_options(cls, parent_dataset=None, user=None):
        """
        Get processor options

        This method by default returns the class's "options" attribute, or an
        empty dictionary. It can be redefined by processors that need more
        fine-grained options, e.g. in cases where the availability of options
        is partially determined by the parent dataset's parameters.

        :param DataSet parent_dataset:  An object representing the dataset that
        the processor would be run on
        :param User user:  Flask user the options will be displayed for, in
        case they are requested for display in the 4CAT web interface. This can
        be used to show some options only to privileges users.
        """
        options = cls.options

        # Update the amount max and help from config
        max_number_videos = int(config.get('video_downloader.MAX_NUMBER_VIDEOS', 1000))
        options['amount']['max'] = max_number_videos
        options['amount']['help'] = "No. of images (max %s)" % max_number_videos

        return options

    @classmethod
    def is_compatible_with(cls, module=None):
        """
        Allow processor TikTok datasets

        :param module: Dataset or processor to determine compatibility with
        """
        return module.type in ["tiktok-search", "tiktok-urls-search"]

    def process(self):
        """
        Reads a file, filtering items that match in the required way, and
        creates a new dataset containing the matching values
        """
        if self.source_dataset.num_rows == 0:
            self.dataset.update_status("No videos to download.", is_final=True)
            self.dataset.finish(0)
            return

        # Process parameters
        amount = self.parameters.get("amount") if self.parameters.get("amount") != 0 else self.source_dataset.num_rows
        max_amount = min(amount, self.source_dataset.num_rows)

        # Prepare staging area for downloads
        results_path = self.dataset.get_staging_area()

        self.dataset.update_status("Downloading TikTok media")
        video_ids_to_download = []
        for original_item, mapped_item in self.source_dataset.iterate_mapped_items(self):
            video_ids_to_download.append(mapped_item.get("id"))

        tiktok_scraper = TikTokScraper()
        loop = asyncio.new_event_loop()
        results = loop.run_until_complete(tiktok_scraper.download_videos(video_ids_to_download, results_path, max_amount, processor=self))

        with results_path.joinpath(".metadata.json").open("w", encoding="utf-8") as outfile:
            json.dump(results, outfile)

        self.write_archive_and_finish(results_path, len(results))

    @staticmethod
    def map_metadata(video_id, data):
        """
        Iterator to yield modified metadata for CSV

        :param str video_id:  string that may contain URLs
        :param dict data:  dictionary with metadata collected previously
        :yield dict:  	  iterator containing reformated metadata
        """
        filename = data.pop("files")[0].get("filename") if "files" in data else None
        row = {
            "video_id": video_id,
            "filename": filename,
            **data
        }
        yield row

class TikTokImageDownloader(BasicProcessor):
    type = "image-downloader-tiktok"  # job type ID
    category = "Visual"  # category
    title = "Download TikTok Images"  # title displayed in UI
    description = "Downloads video/music thumbnails for TikTok; refreshes TikTok data if URLs have expired"
    extension = "zip"

    options = {
        "amount": {
            "type": UserInput.OPTION_TEXT,
            "help": "No. of items (max 1000)",
            "default": 100,
            "min": 0,
            "max": 1000
        },
        "thumb_type": {
            "type": UserInput.OPTION_CHOICE,
            "help": "Media type",
            "options": {
                "thumbnail": "Video Thumbnail",
                "music": "Music Thumbnail",
            },
            "default": "thumbnail"
        }
    }

    @classmethod
    def get_options(cls, parent_dataset=None, user=None):
        """
        Get processor options

        This method by default returns the class's "options" attribute, or an
        empty dictionary. It can be redefined by processors that need more
        fine-grained options, e.g. in cases where the availability of options
        is partially determined by the parent dataset's parameters.

        :param DataSet parent_dataset:  An object representing the dataset that
        the processor would be run on
        :param User user:  Flask user the options will be displayed for, in
        case they are requested for display in the 4CAT web interface. This can
        be used to show some options only to privileges users.
        """
        options = cls.options

        # Update the amount max and help from config
        max_number_images = int(config.get('image_downloader.MAX_NUMBER_IMAGES', 1000))
        options['amount']['max'] = max_number_images
        options['amount']['help'] = "No. of images (max %s)" % max_number_images

        return options

    @classmethod
    def is_compatible_with(cls, module=None):
        """
        Allow processor TikTok datasets

        :param module: Dataset or processor to determine compatibility with
        """
        return module.type in ["tiktok-search", "tiktok-urls-search"]

    def process(self):
        """
        Reads a file, filtering items that match in the required way, and
        creates a new dataset containing the matching values
        """
        if self.source_dataset.num_rows == 0:
            self.dataset.update_status("No images to download.", is_final=True)
            self.dataset.finish(0)
            return

        # Process parameters
        amount = self.parameters.get("amount") if self.parameters.get("amount") != 0 else self.source_dataset.num_rows
        max_amount = min(amount, self.source_dataset.num_rows)
        if self.parameters.get("thumb_type") == "thumbnail":
            url_column = "thumbnail_url"
        elif self.parameters.get("thumb_type") == "music":
            url_column = "music_thumbnail"
        else:
            self.dataset.update_status("No image column selected.", is_final=True)
            self.dataset.finish(0)
            return

        # Prepare staging area for downloads
        results_path = self.dataset.get_staging_area()

        self.dataset.update_status("Downloading TikTok media")
        downloaded_media = 0
        urls_to_refresh = []
        url_to_item_id = {}
        max_fails_exceeded = 0

        # Loop through items and collect URLs
        for original_item, mapped_item in self.source_dataset.iterate_mapped_items(self):
            if downloaded_media >= max_amount:
                break

            url = mapped_item.get(url_column)

            if max_fails_exceeded > 4:
                # Let's just refresh remaining URLs if it is clear the dataset is old
                refresh_tiktok_urls = True
            else:
                refresh_tiktok_urls = False

                # Check if URL missing or expired
                now = int(datetime.datetime.now(tz=datetime.timezone.utc).timestamp())
                if not url or int(parse_qs(urlparse(url).query).get("x-expires", [now])[0]) < now:
                    refresh_tiktok_urls = True
                else:
                    # Collect image
                    try:
                        image, extension = self.collect_image(url)
                        success = self.save_image(image, mapped_item.get("id") + "." + extension, results_path)
                    except FileNotFoundError as e:
                        self.dataset.log(f"{e} for {url}, refreshing")
                        success = False

                    if not success:
                        # Add TikTok post to be refreshed
                        refresh_tiktok_urls = True
                        # Stop checking and refresh all remaining URLs
                        max_fails_exceeded += 1
                    else:
                        self.dataset.update_status(f"Downloaded image for {url}")
                        downloaded_media += 1

            if refresh_tiktok_urls:
                # Add URL to later refresh TikTok data
                tiktok_url = mapped_item.get("tiktok_url")
                if tiktok_url in url_to_item_id:
                    url_to_item_id[tiktok_url].append(mapped_item.get("id"))
                else:
                    urls_to_refresh.append(tiktok_url)
                    url_to_item_id[tiktok_url] = [mapped_item.get("id")]

        if downloaded_media < max_amount and urls_to_refresh:
            # Refresh and collect more images
            tiktok_scraper = TikTokScraper()
            need_more = max_amount - downloaded_media
            last_url_index = 0
            while need_more > 0:
                url_slice = urls_to_refresh[last_url_index: last_url_index + need_more]
                if len(url_slice) == 0:
                    # Ensure there are still URLs to process
                    break
                self.dataset.update_status(f"Refreshing {len(url_slice)} TikTok posts")
                loop = asyncio.new_event_loop()
                # Refresh only number of URLs needed to complete image downloads
                refreshed_items = loop.run_until_complete(tiktok_scraper.request_metadata(url_slice, processor=self))
                self.dataset.update_status(f"Refreshed {len(url_slice)} TikTok posts")

                for refreshed_item in refreshed_items:
                    refreshed_mapped_item = self.source_dataset.get_own_processor().map_item(refreshed_item)

                    # Collect image
                    try:
                        image, extension = self.collect_image(refreshed_mapped_item.get(url_column))
                        success = self.save_image(image, refreshed_mapped_item.get("id") + "." + extension,
                                                  results_path)
                    except FileNotFoundError as e:
                        self.dataset.log(f"{e} for {refreshed_mapped_item.get(url_column)}, skipping")
                        success = False

                    if success:
                        self.dataset.update_status(f"Downloaded image for {url}")
                        downloaded_media += 1
                    else:
                        self.dataset.log(f"Error saving image for {url}, skipping")

                # In case some images failed to download, we update our starting points
                last_url_index += need_more
                need_more = max_amount - downloaded_media

        self.write_archive_and_finish(results_path, downloaded_media)

    @staticmethod
    def save_image(image, image_name, directory_path):
        """
        Save image as image_name to directory_path

        :param Image image:         Opened image object to be saved
        :param str image_name:      Filename for image
        :param Path directory_path: Path where image should be saved
        :return bool:               True if saved successfully, False otherwise
        """
        try:
            image.save(str(directory_path.joinpath(image_name)))
            return True
        except OSError:
            # Some images may need to be converted to RGB to be saved
            try:
                picture = image.convert('RGB')
                picture.save(str(directory_path.joinpath(Path(image_name).with_suffix(".png"))))
                return True
            except OSError:
                return False
        except ValueError:
            return False

    @staticmethod
    def collect_image(url, user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/12.1.1 Safari/605.1.15"):
        """
        :param str url:         String with a validated URL
        :param str user_agent:  String with the desired user agent to be sent as a header
        """
        try:
            response = requests.get(url, stream=True, timeout=20, headers={"User-Agent": user_agent})
        except requests.exceptions.ConnectionError as e:
            raise FileNotFoundError(f"Unable to connect to TikTok {e}")

        if response.status_code != 200 or "image" not in response.headers.get("content-type", ""):
            raise FileNotFoundError(f"Unable to download image; status_code:{response.status_code} content-type:{response.headers.get('content-type', '')}")

        # Process images
        image_io = BytesIO(response.content)
        try:
            picture = Image.open(image_io)
        except UnidentifiedImageError:
            picture = Image.open(image_io.raw)

        # Grab extension from response
        extension = response.headers["Content-Type"].split("/")[-1]

        return picture, extension
