import asyncio
import os
import urllib.parse
import json
import time
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any

from aiohttp import ClientSession
from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer, Horizontal, Vertical
from textual.widgets import Button, Log, Static, Header, Footer, ListItem, ListView
from textual.reactive import reactive
from textual.binding import Binding


class TwitterCrawlStatus:
    def __init__(self, username: str):
        self.username = username
        self.status = "Waiting"
        self.tweets_found = 0
        self.images_found = 0
        self.images_downloaded = 0
        self.error = None
        self.started_at = None
        self.completed_at = None
        self.oldest_id = None
        self.is_complete_fetch = False
        self.pages_fetched = 0

    @property
    def is_running(self) -> bool:
        return self.status == "Running"

    @property
    def is_complete(self) -> bool:
        return self.status in ["Completed", "Failed"]

    @property
    def duration(self) -> Optional[float]:
        if self.started_at is None:
            return None
        end_time = self.completed_at or time.time()
        return end_time - self.started_at

    def start(self):
        self.status = "Running"
        self.started_at = time.time()

    def complete(self):
        self.status = "Completed"
        self.completed_at = time.time()

    def fail(self, error: str):
        self.status = "Failed"
        self.error = error
        self.completed_at = time.time()

    def __str__(self) -> str:
        duration = f" ({self.duration:.1f}s)" if self.duration is not None else ""
        status_line = f"Status: {self.status}{duration}"
        pages = f"Pages: {self.pages_fetched}" if self.pages_fetched > 0 else ""
        counts = f"Tweets: {self.tweets_found} | Images: {self.images_found}/{self.images_downloaded}"
        complete = " (Complete)" if self.is_complete_fetch else ""
        error = f"\nError: {self.error}" if self.error else ""
        return f"{status_line}{complete}\n{counts} | {pages}{error}"


class TwitterUserItem(ListItem):
    def __init__(self, username: str):
        super().__init__()
        self.username = username
        
    def render(self) -> str:
        return self.username


class CrawlStatusWidget(Static):
    status = reactive(None)

    def __init__(self, status: TwitterCrawlStatus = None):
        super().__init__()
        self.status = status
        if status is not None:
            self.update(f"@{status.username}\n{status}")
        else:
            self.update("No status available")

    def watch_status(self, status: TwitterCrawlStatus) -> None:
        if status is not None:
            self.update(f"@{status.username}\n{status}")
        else:
            self.update("No status available")


class TwitterCrawlerApp(App):
    TITLE = "Twitter Image Crawler"
    CSS = """
    #sidebar {
        width: 25%;
        min-width: 15;
        background: $surface-lighten-1;
    }
    
    #details {
        width: 75%;
    }
    
    #status-log {
        height: 70%;
        border: solid $primary;
    }
    
    .user-item {
        padding: 1 2;
        height: 3;
    }
    
    .user-item:hover {
        background: $primary-lighten-2;
    }
    
    .user-item.-selected {
        background: $primary-lighten-1;
    }
    
    .status-widget {
        padding: 1;
        margin: 1;
        height: auto;
        border: solid $primary-background;
        background: $surface;
    }
    
    CrawlStatusWidget {
        height: auto;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "run_selected", "Run Selected"),
        Binding("a", "run_all", "Run All"),
        Binding("s", "stop_all", "Stop All"),
    ]

    def __init__(self):
        super().__init__()
        self.usernames = []
        self.crawl_statuses: Dict[str, TwitterCrawlStatus] = {}
        self.session = None
        self.selected_username = None
        self.running_tasks = []

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal():
            with ScrollableContainer(id="sidebar"):
                yield ListView(id="user-list")

            with Vertical(id="details"):
                yield Static("Select a username to see details", id="status-display")
                with ScrollableContainer(id="status-log"):
                    yield Log(id="log")

                with Horizontal():
                    yield Button("Run Selected", id="run-selected")
                    yield Button("Run All", id="run-all")
                    yield Button("Stop All", id="stop-all")

        yield Footer()

    async def on_mount(self) -> None:
        self.load_usernames()
        self.session = initialize_session()

        user_list = self.query_one("#user-list", ListView)
        for username in self.usernames:
            user_list.append(TwitterUserItem(username))
            self.crawl_statuses[username] = TwitterCrawlStatus(username)
        
        # Start crawling automatically
        self.log_gui("Starting automatic crawling of all accounts...")
        self.action_run_all()

    def load_usernames(self) -> None:
        try:
            with open("inputs/twitter_usernames.json") as f_twitter_usernames:
                self.usernames = json.load(f_twitter_usernames)
            self.log_gui(f"Loaded {len(self.usernames)} usernames")
        except Exception as e:
            self.log_gui(f"Error loading usernames: {e}")
            self.usernames = []

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, TwitterUserItem):
            self.selected_username = item.username
            status = self.crawl_statuses[item.username]
            status_display = self.query_one("#status-display", Static)
            status_display.update(f"@{status.username}\n{status}")

    def action_run_selected(self) -> None:
        if self.selected_username:
            self.run_crawler(self.selected_username)

    def action_run_all(self) -> None:
        for username in self.usernames:
            self.run_crawler(username)

    def action_stop_all(self) -> None:
        for task in self.running_tasks:
            if not task.done():
                task.cancel()
        self.log_gui("Stopped all running crawls")

    def run_crawler(self, username: str) -> None:
        status = self.crawl_statuses[username]
        if status.is_running:
            self.log_gui(f"Already crawling @{username}")
            return

        status.start()
        self.update_status_widget(username)

        self.log_gui(f"Starting crawl for @{username}")
        task = asyncio.create_task(self.crawl_user(username, status))
        self.running_tasks.append(task)

    def update_status_widget(self, username: str) -> None:
        status_display = self.query_one("#status-display", Static)
        if self.selected_username == username:
            status = self.crawl_statuses[username]
            status_display.update(f"@{status.username}\n{status}")

    def log_gui(self, message: str) -> None:
        log_widget = self.query_one("#log", Log)
        log_widget.write(message + "\n")

    async def crawl_user(self, username: str, status: TwitterCrawlStatus) -> None:
        try:
            # Create intermediates base directory and user directory
            intermediates_base = Path("intermediates")
            intermediates_base.mkdir(exist_ok=True)
            
            twitter_dir = intermediates_base / "twitter"
            twitter_dir.mkdir(exist_ok=True)
            
            output_dir = twitter_dir / username
            output_dir.mkdir(exist_ok=True)
            
            # Check for existing data file
            data_file = output_dir / f"{username}_tweets.json"
            all_tweets = []
            
            # Try to load existing data if available
            if data_file.exists():
                try:
                    self.log_gui(f"Found existing data for @{username}, loading...")
                    with open(data_file, "r") as f:
                        existing_data = json.load(f)
                        all_tweets = existing_data.get("tweets", [])
                        status.tweets_found = len(all_tweets)
                        status.oldest_id = existing_data.get("oldest_id")
                        status.is_complete_fetch = existing_data.get("is_complete", False)
                        self.log_gui(f"Loaded {len(all_tweets)} existing tweets for @{username}")
                        
                        # If we already have all tweets, we can skip fetching
                        if status.is_complete_fetch:
                            self.log_gui(f"Already have all tweets for @{username}, skipping fetch")
                            
                except Exception as e:
                    self.log_gui(f"Error loading existing data for @{username}: {e}")
                    self.log_gui("Will start fresh fetch")
            
            # Continue fetching tweets until we've reached the end
            if not status.is_complete_fetch:
                self.log_gui(f"Starting tweet fetch for @{username}")
                max_id = status.oldest_id
                
                # Keep track of all tweets across pagination
                while True:
                    status.pages_fetched += 1
                    self.update_status_widget(username)
                    
                    # Fetch a page of tweets
                    page_tweets, oldest_id, is_complete = await fetch_tweets(
                        self.session, username, max_id, self.log_gui
                    )
                    
                    if page_tweets:
                        self.log_gui(f"Retrieved {len(page_tweets)} tweets (page {status.pages_fetched})")
                        all_tweets.extend(page_tweets)
                        status.tweets_found = len(all_tweets)
                        self.update_status_widget(username)
                        
                        # Atomic save after each page
                        interim_data = {
                            "username": username,
                            "tweets": all_tweets,
                            "oldest_id": oldest_id,
                            "is_complete": False,
                            "last_updated": time.time(),
                            "pages_fetched": status.pages_fetched
                        }
                        
                        # Write atomically using our helper function
                        atomic_write_json(
                            interim_data, 
                            data_file,
                            self.log_gui
                        )
                        self.log_gui(f"Saved interim data with {len(all_tweets)} tweets")
                        
                        # Set up for next page
                        if oldest_id and not is_complete:
                            max_id = oldest_id
                            status.oldest_id = oldest_id
                            # Avoid hitting rate limits
                            await asyncio.sleep(1)
                        else:
                            # We've reached the end
                            status.is_complete_fetch = True
                            break
                    else:
                        # No tweets in this page
                        status.is_complete_fetch = True
                        break
                
                # Final save after all pages are fetched
                final_data = {
                    "username": username,
                    "tweets": all_tweets,
                    "oldest_id": status.oldest_id,
                    "is_complete": status.is_complete_fetch,
                    "last_updated": time.time(),
                    "pages_fetched": status.pages_fetched
                }
                
                # Write final data atomically using our helper
                atomic_write_json(
                    final_data,
                    data_file,
                    self.log_gui
                )
                self.log_gui(f"Completed tweet fetch for @{username}, found {len(all_tweets)} tweets")
            
            # Process tweets for media
            self.log_gui(f"Processing {len(all_tweets)} tweets for @{username} to extract media")
            
            for i, tweet in enumerate(all_tweets):
                tweet_id = tweet.get("id_str") or tweet.get("id", "")
                tweet_text = tweet.get("full_text") or tweet.get("text", "")
                truncated_text = (tweet_text[:50] + "...") if tweet_text and len(tweet_text) > 50 else tweet_text
                
                # Debug the entire tweet structure for the first tweet
                if i == 0:
                    self.log_gui(f"First tweet structure keys: {tweet.keys()}")
                
                # Get media from entities
                entities = tweet.get("entities", {})
                media_items = entities.get("media", [])
                
                # Also check extended_entities which may contain media
                extended_entities = tweet.get("extended_entities", {})
                if extended_entities and "media" in extended_entities:
                    extended_media = extended_entities.get("media", [])
                    if i == 0 and extended_media:
                        self.log_gui(f"Found extended_entities media: {len(extended_media)} items")
                    media_items.extend(extended_media)
                
                # Check for URLs that might be images
                urls = []
                if "entities" in tweet and "urls" in tweet["entities"]:
                    urls = tweet["entities"]["urls"]
                    # Find image URLs in expanded_url fields
                    for url_obj in urls:
                        expanded_url = url_obj.get("expanded_url", "")
                        if expanded_url and any(img_ext in expanded_url.lower() for img_ext in ['.jpg', '.jpeg', '.png', '.gif']):
                            if i == 0:
                                self.log_gui(f"Found image URL in expanded_url: {expanded_url}")
                            media_items.append({
                                "media_url": expanded_url,
                                "type": "photo"
                            })
                
                if media_items:
                    self.log_gui(f"Found {len(media_items)} media items in tweet {tweet_id}")
                    status.images_found += len(media_items)
                    self.update_status_widget(username)

                    for j, media_item in enumerate(media_items):
                        # Try different fields where the URL might be
                        # In tweets with media, there is a direct URL available
                        media_url = (media_item.get("media_url_https") or 
                                    media_item.get("media_url") or 
                                    media_item.get("expanded_url"))
                        
                        if media_url:
                            image_path = output_dir / f"{tweet_id}_{j}.jpg"
                            
                            # Only download if it doesn't already exist
                            if not image_path.exists():
                                try:
                                    self.log_gui(f"Downloading image: {media_url}")
                                    await self.download_image(media_url, image_path)
                                    status.images_downloaded += 1
                                    self.update_status_widget(username)
                                except Exception as e:
                                    self.log_gui(f"Error downloading image from @{username}: {e}")
                            else:
                                # Count already downloaded image
                                status.images_downloaded += 1
                                self.update_status_widget(username)

                if i % 10 == 0 or i == len(all_tweets) - 1:
                    self.log_gui(f"Processed tweet {i+1}/{len(all_tweets)} for @{username}")

            # Process and save data for Hugo
            self.process_data_for_hugo(username, all_tweets, output_dir)
            
            status.complete()
            self.log_gui(f"Completed crawl for @{username}")
        except Exception as e:
            status.fail(str(e))
            self.log_gui(f"Failed crawl for @{username}: {e}")
            import traceback
            self.log_gui(traceback.format_exc())
        finally:
            self.update_status_widget(username)
            
    def process_data_for_hugo(self, username: str, tweets: List[Dict], images_dir: Path) -> None:
        """Process crawled data and save it in the Hugo data directory."""
        try:
            # Create Hugo data directory if it doesn't exist
            data_dir = Path("data/twitter")
            data_dir.mkdir(parents=True, exist_ok=True)
            
            # Create a JSON file with tweet data and image references
            processed_data = {
                "username": username,
                "profile_url": f"https://twitter.com/{username}",
                "tweet_count": len(tweets),
                "tweets": []
            }
            
            self.log_gui(f"Processing {len(tweets)} tweets for @{username} for Hugo data")
            
            # First, create a temp file path
            temp_file = data_dir / f"{username}.tmp.json"
            output_file = data_dir / f"{username}.json"
            
            # Process tweets in batches to avoid memory issues
            batch_size = 100
            tweet_batches = [tweets[i:i+batch_size] for i in range(0, len(tweets), batch_size)]
            
            self.log_gui(f"Processing {len(tweet_batches)} batches of tweets")
            
            for batch_idx, tweet_batch in enumerate(tweet_batches):
                batch_tweets = []
                self.log_gui(f"Processing batch {batch_idx+1}/{len(tweet_batches)} ({len(tweet_batch)} tweets)")
                
                for tweet in tweet_batch:
                    # Extract data using the SocialData API structure
                    tweet_id = tweet.get("id_str") or tweet.get("id", "")
                    tweet_text = tweet.get("full_text") or tweet.get("text", "")
                    created_at = tweet.get("tweet_created_at") or tweet.get("created_at", "")
                    
                    tweet_data = {
                        "id": tweet_id,
                        "text": tweet_text,
                        "created_at": created_at,
                        "url": f"https://twitter.com/{username}/status/{tweet_id}",
                        "images": []
                    }
                    
                    # Extract images from entities if available
                    entities = tweet.get("entities", {})
                    
                    # Try to find media in different places
                    media_items = entities.get("media", [])
                    
                    # Also check extended_entities which may contain media
                    extended_entities = tweet.get("extended_entities", {})
                    if extended_entities and "media" in extended_entities:
                        media_items.extend(extended_entities.get("media", []))
                        
                    # Also look for URLs that might be images
                    urls = entities.get("urls", [])
                    for url_obj in urls:
                        expanded_url = url_obj.get("expanded_url", "")
                        if expanded_url and any(img_ext in expanded_url.lower() for img_ext in ['.jpg', '.jpeg', '.png', '.gif']):
                            media_items.append({
                                "media_url": expanded_url,
                                "type": "photo"
                            })
                    
                    if media_items:
                        for j, media_item in enumerate(media_items):
                            media_url = (media_item.get("media_url_https") or 
                                        media_item.get("media_url") or 
                                        media_item.get("expanded_url"))
                            
                            if media_url:
                                image_filename = f"{tweet_id}_{j}.jpg"
                                image_path = images_dir / image_filename
                                
                                # Add image URL to tweet data
                                image_data = {
                                    "filename": image_filename,
                                    "url": media_url,
                                    "type": media_item.get("type", "photo"),
                                    "downloaded": image_path.exists()
                                }
                                
                                if image_path.exists():
                                    image_data["path"] = str(image_path.relative_to(Path.cwd()))
                                
                                tweet_data["images"].append(image_data)
                    
                    batch_tweets.append(tweet_data)
                
                # Update processed data with this batch of tweets
                processed_data["tweets"].extend(batch_tweets)
                
                # Atomically update the file after each batch
                atomic_write_json(
                    processed_data,
                    output_file,
                    self.log_gui
                )
                
                self.log_gui(f"Saved batch {batch_idx+1}/{len(tweet_batches)}, " 
                           f"total {len(processed_data['tweets'])}/{len(tweets)} tweets processed")
            
            # Final save after all batches are processed
            # Update timestamp for last processed
            processed_data["last_processed"] = time.time()
            
            # Final atomic write
            atomic_write_json(
                processed_data,
                output_file,
                self.log_gui
            )
                
            self.log_gui(f"Completed saving processed data for @{username} to {output_file}")
        except Exception as e:
            self.log_gui(f"Error processing data for Hugo: {e}")
            import traceback
            self.log_gui(traceback.format_exc())

    async def download_image(self, url: str, path: Path) -> None:
        async with self.session.get(url) as response:
            if response.status == 200:
                with open(path, "wb") as f:
                    f.write(await response.read())
            else:
                raise Exception(f"Failed to download image: {response.status}")


async def fetch_tweets(
    session: ClientSession, 
    username: str, 
    max_id: Optional[str] = None,
    log_callback = None
) -> (List[Dict], Optional[str], bool):
    """
    Fetch tweets from a user and return them as a list of tweet objects.
    
    Args:
        session: The client session to use for the request
        username: The Twitter username to fetch tweets for
        max_id: The maximum tweet ID to fetch (for pagination)
        log_callback: Optional callback function for logging
    
    Returns:
        tuple: (tweets list, oldest tweet ID, is_complete flag)
    """
    query = f"from:{username}"
    if max_id:
        query += f" max_id:{max_id}"
    
    encoded_query = urllib.parse.quote_plus(query)
    
    try:
        log_msg = f"Fetching tweets for {username}" + (f" with max_id: {max_id}" if max_id else "")
        print(log_msg)
        if log_callback:
            log_callback(log_msg)
            
        async with session.get(f"/twitter/search?query={encoded_query}") as response:
            if response.status == 200:
                data = await response.json()
                tweets = data.get("tweets", [])
                
                log_msg = f"Retrieved {len(tweets)} tweets for {username}"
                print(log_msg)
                if log_callback:
                    log_callback(log_msg)
                
                # Check if we've reached the end (no more tweets)
                is_complete = len(tweets) == 0
                
                # Get the oldest tweet ID for pagination
                oldest_id = None
                if tweets:
                    # Sort tweets by ID to find the oldest one
                    tweet_ids = [int(t.get("id_str", 0) or t.get("id", 0)) for t in tweets]
                    if tweet_ids:
                        oldest_id = str(min(tweet_ids))
                        
                    # Log a sample tweet to see the structure (only on first page)
                    if not max_id:
                        print(f"Sample tweet keys: {tweets[0].keys()}")
                        print(f"Sample tweet content: {json.dumps(tweets[0], indent=2)[:500]}...")
                        if log_callback:
                            log_callback(f"Sample tweet keys: {tweets[0].keys()}")
                
                return tweets, oldest_id, is_complete
            else:
                error_text = await response.text()
                error_msg = f"API error for {username}: {response.status} - {error_text}"
                print(error_msg)
                if log_callback:
                    log_callback(error_msg)
                raise Exception(f"API returned status {response.status}: {error_text}")
    except Exception as e:
        error_msg = f"Exception fetching tweets for {username}: {e}"
        print(error_msg)
        if log_callback:
            log_callback(error_msg)
        raise Exception(f"Error fetching tweets: {e}")


def atomic_write_json(data: Any, target_path: Path, log_callback=None) -> bool:
    """
    Write JSON data atomically to ensure data integrity even with power failures.
    
    Uses a temporary file in the same directory as the target file, then renames it
    in a single atomic operation to replace the target file.
    
    Args:
        data: The data to write as JSON
        target_path: The destination file path
        log_callback: Optional callback for logging
        
    Returns:
        bool: True if successful, False otherwise
    """
    target_dir = target_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Use the same directory as the target to ensure atomic rename works across filesystems
        with tempfile.NamedTemporaryFile(mode='w', dir=target_dir, delete=False, suffix='.tmp') as tf:
            temp_path = Path(tf.name)
            json.dump(data, tf, indent=2)
            tf.flush()
            os.fsync(tf.fileno())  # Ensure data is written to disk
            
        # Atomic rename
        temp_path.rename(target_path)
        
        if log_callback:
            log_callback(f"Atomically wrote data to {target_path}")
        return True
    except Exception as e:
        if log_callback:
            log_callback(f"Error writing data atomically: {e}")
        # Clean up temp file if it exists
        try:
            if 'temp_path' in locals() and temp_path.exists():
                temp_path.unlink()
        except:
            pass
        return False


def initialize_session() -> ClientSession:
    if (socialdata_api_key := os.getenv("SOCIALDATA_API_KEY")) is None:
        raise RuntimeError(
            "Please go to https://socialdata.tools to generate and set SOCIALDATA_API_KEY."
        )
    return ClientSession(
        base_url="https://api.socialdata.tools/",
        headers={"Authorization": "Bearer " + socialdata_api_key},
    )


async def main():
    app = TwitterCrawlerApp()
    await app.run_async()


if __name__ == "__main__":
    asyncio.run(main())
