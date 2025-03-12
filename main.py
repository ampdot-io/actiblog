import asyncio
import os
import urllib.parse
import json
import time
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any

import aiohttp
from aiohttp import ClientSession
from textual.app import App, ComposeResult
from textual.containers import ScrollableContainer, Horizontal, Vertical
from textual.widgets import Button, Log, Static, Header, Footer, ListItem, ListView, Input
from textual.reactive import reactive
from textual.binding import Binding
from textual.message import Message


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
        self.estimated_total_tweets = 0  # Total tweets based on user profile
        # Track download attempts and success status for each URL
        self.tweet_id_to_url_attempts = {}  # Maps tweet_id -> {url -> attempt_count}
        self.tweet_id_to_url_success = {}   # Maps tweet_id -> {url -> success_bool}
        
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
        
    @property
    def progress_percentage(self) -> int:
        """Calculate progress percentage based on estimated total tweets"""
        if self.estimated_total_tweets <= 0:
            return 0
        return min(100, int((self.tweets_found / self.estimated_total_tweets) * 100))

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
        progress = f"{self.progress_percentage}%" if self.estimated_total_tweets > 0 else ""
        pages = f"Pages: {self.pages_fetched}" if self.pages_fetched > 0 else ""
        counts = f"Tweets: {self.tweets_found}/{self.estimated_total_tweets} | Images: {self.images_found}/{self.images_downloaded}"
        stats = f"{counts} | Progress: {progress}" if progress else counts
        complete = " (Complete)" if self.is_complete_fetch else ""
        error = f"\nError: {self.error}" if self.error else ""
        return f"{status_line}{complete}\n{stats} | {pages}{error}"


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
    
    .log-filter {
        margin-bottom: 1;
    }
    
    #add-user-container {
        margin-top: 1;
        border-top: solid $primary;
        padding-top: 1;
    }
    
    #add-user-input {
        margin-bottom: 1;
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "run_selected", "Run Selected"),
        Binding("a", "run_all", "Run All"),
        Binding("s", "stop_all", "Stop All"),
        Binding("d", "retry_downloads", "Retry Downloads"),
        Binding("n", "focus_new_user", "Add User"),
    ]

    def __init__(self):
        super().__init__()
        self.usernames = []
        self.crawl_statuses: Dict[str, TwitterCrawlStatus] = {}
        self.session = None
        self.image_session = None
        self.selected_username = None
        self.running_tasks = []
        self.current_log_filter = None
        
    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal():
            with ScrollableContainer(id="sidebar"):
                yield ListView(id="user-list")
                with Vertical(id="add-user-container"):
                    from textual.widgets import Input
                    yield Input(placeholder="Enter username to add", id="add-user-input")
                    yield Button("Add User", id="add-user-button", variant="primary")

            with Vertical(id="details"):
                yield Static("Select a username to see details", id="status-display")
                
                # Add log filter options
                with Horizontal(classes="log-filter"):
                    yield Static("Log Filter: ", classes="log-filter-label")
                    yield Button("All Logs", id="log-filter-all", variant="success")
                    yield Button("Current User Only", id="log-filter-user", variant="default")
                    yield Static("", id="log-filter-status", classes="log-filter-status")
                    
                with ScrollableContainer(id="status-log"):
                    yield Log(id="status-log-content")

                with Horizontal():
                    yield Button("Run Selected", id="run-selected")
                    yield Button("Retry Downloads", id="retry-downloads")

        yield Footer()

    async def on_mount(self) -> None:
        self.load_usernames()
        # Initialize API session for Twitter API
        self.session = initialize_session()
        
        # Initialize a separate session for image downloads with limited connections
        # TCPConnector limits max number of concurrent connections
        connector = aiohttp.TCPConnector(limit=3)  # Max 3 concurrent connections
        self.image_session = aiohttp.ClientSession(connector=connector)
        
        # Initialize a virtual "All" user for aggregate stats
        self.crawl_statuses["All"] = TwitterCrawlStatus("All")
        
        user_list = self.query_one("#user-list", ListView)
        
        # Add "All" user first
        user_list.append(TwitterUserItem("All"))
        
        # Add actual users
        for username in self.usernames:
            user_list.append(TwitterUserItem(username))
            self.crawl_statuses[username] = TwitterCrawlStatus(username)
            
            # Start fetching user profile info in the background to get tweet counts
            asyncio.create_task(self.fetch_user_profile_info(username))
        
        # Set "All" user as initially selected
        user_list.index = 0
        self.selected_username = "All"
        self.update_status_widget("All")
        
        # Configure log filter
        self.current_log_filter = None  # No filter by default
        
        # Start crawling automatically
        self.log_gui("Starting automatic crawling of all accounts...")
        self.action_run_all()
        
    async def fetch_user_profile_info(self, username: str) -> None:
        """Fetch user profile information to get tweet count and other stats."""
        try:
            self.log_gui(f"Fetching profile info for @{username}...")
            
            async with self.session.get(f"/twitter/user/{username}") as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if "statuses_count" in data:
                        status = self.crawl_statuses[username]
                        status.estimated_total_tweets = data["statuses_count"]
                        self.log_gui(f"User @{username} has approximately {status.estimated_total_tweets} tweets")
                        
                        # Update the status display if this user is selected
                        if self.selected_username == username:
                            self.update_status_widget(username)
                        
                        # Update the "All" aggregate status
                        self.update_all_status()
                else:
                    error_text = await response.text()
                    self.log_gui(f"Error fetching profile for @{username}: {response.status} - {error_text}")
        except Exception as e:
            self.log_gui(f"Exception fetching profile for @{username}: {e}")
            
    def update_all_status(self) -> None:
        """Update the aggregate 'All' status based on individual user stats."""
        all_status = self.crawl_statuses["All"]
        
        # Reset counters
        all_status.tweets_found = 0
        all_status.images_found = 0 
        all_status.images_downloaded = 0
        all_status.estimated_total_tweets = 0
        all_status.pages_fetched = 0
        
        # Set status based on if any users are running
        any_running = any(status.is_running for username, status in self.crawl_statuses.items() if username != "All")
        all_complete = all(status.is_complete for username, status in self.crawl_statuses.items() if username != "All")
        
        if any_running:
            all_status.status = "Running"
            if all_status.started_at is None:
                all_status.started_at = time.time()
        elif all_complete and len(self.usernames) > 0:
            all_status.status = "Completed"
            if all_status.completed_at is None:
                all_status.completed_at = time.time()
                
        # Aggregate stats
        for username, status in self.crawl_statuses.items():
            if username != "All":
                all_status.tweets_found += status.tweets_found
                all_status.images_found += status.images_found
                all_status.images_downloaded += status.images_downloaded
                all_status.estimated_total_tweets += status.estimated_total_tweets
                all_status.pages_fetched += status.pages_fetched
                
        # Update if All is currently displayed
        if self.selected_username == "All":
            self.update_status_widget("All")

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
            previous_username = self.selected_username
            self.selected_username = item.username
            status = self.crawl_statuses[item.username]
            status_display = self.query_one("#status-display", Static)
            status_display.update(f"@{status.username}\n{status}")
            
            # Update the log filter button state if Current User filter is active
            user_btn = self.query_one("#log-filter-user", Button)
            if user_btn.variant == "success":
                # If Current User filter was active, update it to the newly selected user
                self.set_log_filter(self.selected_username)
                # Inform the user that the filter has been updated
                print(f"Log filter updated to newly selected user: {self.selected_username}")
                
            # Debug print
            print(f"User selection changed from {previous_username} to {self.selected_username}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "run-selected":
            self.action_run_selected()
        elif button_id == "run-all":
            self.action_run_all()
        elif button_id == "stop-all":
            self.action_stop_all()
        elif button_id == "retry-downloads":
            self.action_retry_downloads()
        elif button_id == "log-filter-all":
            self.set_log_filter(None)
        elif button_id == "log-filter-user":
            self.set_log_filter(self.selected_username)
        elif button_id == "add-user-button":
            self.add_new_user()
            
    def set_log_filter(self, username: Optional[str]) -> None:
        """Set the current log filter to show logs only for the given username."""
        # Update the current filter
        self.current_log_filter = username
        
        # Update button styles to show which filter is active
        all_btn = self.query_one("#log-filter-all", Button)
        user_btn = self.query_one("#log-filter-user", Button)
        
        # Clear the log widget first to start fresh
        log_widget = self.query_one("#status-log-content", Log)
        log_widget.clear()
        
        if username is None:
            all_btn.variant = "success"
            user_btn.variant = "default"
            self.log_gui("Showing logs for all users")
            print(f"Log filter set to: None (all users)")
        else:
            all_btn.variant = "default"
            user_btn.variant = "success"
            # Don't use log_gui here to avoid filtering out this message
            log_widget.write(f"Showing logs for @{username} only\n")
            print(f"Log filter set to: {username}")
            
    def action_focus_new_user(self) -> None:
        """Focus the add user input field."""
        input_field = self.query_one("#add-user-input", Input)
        input_field.focus()
        
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle when the user presses Enter in the input field."""
        if event.input.id == "add-user-input":
            self.add_new_user()
        
    def add_new_user(self) -> None:
        """Add a new user from the input field and automatically start crawling."""
        from textual.widgets import Input
        
        input_field = self.query_one("#add-user-input", Input)
        new_username = input_field.value.strip()
        
        # Validate username
        if not new_username:
            self.log_gui("Please enter a username to add")
            return
            
        # Remove @ if user included it
        if new_username.startswith('@'):
            new_username = new_username[1:]
        
        # Check if user already exists
        if new_username in self.usernames:
            self.log_gui(f"User @{new_username} is already in the list")
            return
            
        # Add to usernames list
        self.usernames.append(new_username)
        
        # Save to file
        try:
            with open("inputs/twitter_usernames.json", "w") as f:
                json.dump(self.usernames, f, indent=4)
            self.log_gui(f"Added @{new_username} to the usernames list")
        except Exception as e:
            self.log_gui(f"Error saving usernames: {e}")
            return
            
        # Initialize crawl status
        self.crawl_statuses[new_username] = TwitterCrawlStatus(new_username)
        
        # Start fetching profile info
        asyncio.create_task(self.fetch_user_profile_info(new_username))
        
        # Add to UI list
        user_list = self.query_one("#user-list", ListView)
        user_list.append(TwitterUserItem(new_username))
        
        # Clear input field
        input_field.value = ""
        
        # Automatically start crawling the new user
        self.log_gui(f"Automatically starting crawl for newly added user @{new_username}")
        self.run_crawler(new_username)

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
        
    def action_retry_downloads(self) -> None:
        """Retry any failed downloads for the selected username."""
        if self.selected_username:
            self.log_gui(f"Manually retrying downloads for @{self.selected_username}")
            status = self.crawl_statuses[self.selected_username]
            
            # Create a task to retry downloads
            task = asyncio.create_task(self.manual_retry_downloads(self.selected_username, status))
            self.running_tasks.append(task)
            
    async def manual_retry_downloads(self, username: str, status: TwitterCrawlStatus) -> None:
        """Manually retry failed downloads for a specific user."""
        try:
            # Set up paths
            intermediates_base = Path("intermediates")
            twitter_dir = intermediates_base / "twitter"
            output_dir = twitter_dir / username
            
            # Load tweets from data file
            data_file = output_dir / f"{username}_tweets.json"
            all_tweets = []
            media_stats = {
                "error_count": 0,
                "username": username
            }
            
            if data_file.exists():
                try:
                    with open(data_file, "r") as f:
                        existing_data = json.load(f)
                        all_tweets = existing_data.get("tweets", [])
                        self.log_gui(f"Loaded {len(all_tweets)} tweets for retry")
                        
                        # Make sure we have the URL tracking dictionaries
                        if not status.tweet_id_to_url_attempts and "tweet_id_to_url_attempts" in existing_data:
                            status.tweet_id_to_url_attempts = existing_data["tweet_id_to_url_attempts"]
                            
                        if not status.tweet_id_to_url_success and "tweet_id_to_url_success" in existing_data:
                            status.tweet_id_to_url_success = existing_data["tweet_id_to_url_success"]
                except Exception as e:
                    self.log_gui(f"Error loading tweets for retry: {e}")
                    return
            else:
                self.log_gui(f"No data file found for @{username}")
                return
                
            # Retry downloads
            await self.retry_failed_downloads(username, output_dir, status, media_stats, all_tweets)
            
            # Update data file with new attempt counts
            existing_data["tweet_id_to_url_attempts"] = status.tweet_id_to_url_attempts
            existing_data["tweet_id_to_url_success"] = status.tweet_id_to_url_success
            
            # Write back to file
            atomic_write_json(existing_data, data_file, self.log_gui)
            
            self.log_gui(f"Completed manual retry for @{username}")
            # Update the "All" status
            self.update_all_status()
            
        except Exception as e:
            self.log_gui(f"Error during manual retry: {e}")
            import traceback
            self.log_gui(traceback.format_exc())
        
    async def on_unmount(self) -> None:
        """Clean up resources when app is closing."""
        self.log_gui("Shutting down sessions...")
        
        # Close both sessions
        if self.session:
            await self.session.close()
        
        if self.image_session:
            await self.image_session.close()
            
        self.log_gui("Sessions closed")

    def run_crawler(self, username: str) -> None:
        # Skip if "All" is selected - it's just a virtual user
        if username == "All":
            self.action_run_all()
            return
            
        status = self.crawl_statuses[username]
        if status.is_running:
            self.log_gui(f"Already crawling @{username}")
            return

        status.start()
        self.update_status_widget(username)
        self.update_all_status()  # Update the All view

        self.log_gui(f"Starting crawl for @{username}")
        task = asyncio.create_task(self.crawl_user(username, status))
        self.running_tasks.append(task)

    def update_status_widget(self, username: str) -> None:
        status_display = self.query_one("#status-display", Static)
        if self.selected_username == username:
            status = self.crawl_statuses[username]
            status_display.update(f"@{status.username}\n{status}")
            
        # Update the "All" status whenever any user's status changes
        if username != "All":
            self.update_all_status()

    def log_gui(self, message: str) -> None:
        try:
            log_widget = self.query_one("#status-log-content", Log)
            log_widget.write(message + "\n")
        except Exception as e:
            print(f"Error writing to log: {e}")
            print(message)

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
                        
                        # Load image download tracking data if available
                        if "tweet_id_to_url_attempts" in existing_data:
                            status.tweet_id_to_url_attempts = existing_data["tweet_id_to_url_attempts"]
                            self.log_gui(f"Loaded existing attempt data for {len(status.tweet_id_to_url_attempts)} tweets")
                        
                        if "tweet_id_to_url_success" in existing_data:
                            status.tweet_id_to_url_success = existing_data["tweet_id_to_url_success"]
                            self.log_gui(f"Loaded existing success data for {len(status.tweet_id_to_url_success)} tweets")
                        
                        # If we already have all tweets, we can skip fetching
                        if status.is_complete_fetch:
                            self.log_gui(f"Already have all tweets for @{username}, skipping fetch")
                            
                except Exception as e:
                    self.log_gui(f"Error loading existing data for @{username}: {e}")
                    self.log_gui("Will start fresh fetch")
            
            # Initialize media statistics
            media_stats = {
                "total_tweets_with_media": 0,
                "total_media_items": 0,
                "media_types": {},
                "username": username,
                "tweet_count": len(all_tweets),
                "processed_tweets": 0,
            }
            
            # Check which tweets have already been processed for images
            processed_tweet_ids = set()
            if data_file.exists():
                try:
                    with open(data_file, "r") as f:
                        existing_data = json.load(f)
                        # Extract IDs of tweets that have been processed for images
                        for tweet in existing_data.get("tweets", []):
                            if tweet.get("images_processed", False):
                                processed_tweet_ids.add(tweet.get("id_str") or tweet.get("id", ""))
                                media_stats["processed_tweets"] += 1
                except Exception as e:
                    self.log_gui(f"Error loading processed tweet IDs: {e}")
            
            # Track which tweets in the all_tweets list have been processed for images
            for tweet in all_tweets:
                tweet_id = tweet.get("id_str") or tweet.get("id", "")
                if tweet_id in processed_tweet_ids:
                    tweet["images_processed"] = True
                else:
                    tweet["images_processed"] = False
            
            # Process tweets that have already been fetched but not processed for images
            if all_tweets and any(not t.get("images_processed", False) for t in all_tweets):
                await self.process_tweets_for_media(username, all_tweets, output_dir, status, media_stats, data_file, processed_tweet_ids)
            
            # Look for failed downloads that need to be retried
            self.log_gui(f"Checking for failed downloads that need to be retried...")
            await self.retry_failed_downloads(username, output_dir, status, media_stats, all_tweets)
            
            # Continue fetching new tweets until we've reached the end
            if not status.is_complete_fetch:
                self.log_gui(f"Starting tweet fetch for @{username}")
                max_id = status.oldest_id
                
                while True:
                    status.pages_fetched += 1
                    self.update_status_widget(username)
                    
                    # Fetch a page of tweets
                    page_tweets, oldest_id, is_complete = await fetch_tweets(
                        self.session, username, max_id, self.log_gui
                    )
                    
                    if page_tweets:
                        self.log_gui(f"Retrieved {len(page_tweets)} tweets (page {status.pages_fetched})")
                        
                        # Mark all new tweets as not processed for images
                        for tweet in page_tweets:
                            tweet["images_processed"] = False
                        
                        # Add to our list of all tweets
                        all_tweets.extend(page_tweets)
                        status.tweets_found = len(all_tweets)
                        self.update_status_widget(username)
                        
                        # Process this page of tweets for images immediately
                        await self.process_tweets_for_media(username, page_tweets, output_dir, status, media_stats, data_file, processed_tweet_ids)
                        
                        # Update the data file with all tweets so far
                        interim_data = {
                            "username": username,
                            "tweets": all_tweets,
                            "oldest_id": oldest_id,
                            "is_complete": False,
                            "last_updated": time.time(),
                            "pages_fetched": status.pages_fetched,
                            "media_stats": media_stats,
                            "tweet_id_to_url_attempts": status.tweet_id_to_url_attempts,
                            "tweet_id_to_url_success": status.tweet_id_to_url_success
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
                    "pages_fetched": status.pages_fetched,
                    "media_stats": media_stats,
                    "tweet_id_to_url_attempts": status.tweet_id_to_url_attempts,
                    "tweet_id_to_url_success": status.tweet_id_to_url_success
                }
                
                # Write final data atomically using our helper
                atomic_write_json(
                    final_data,
                    data_file,
                    self.log_gui
                )
                self.log_gui(f"Completed tweet fetch for @{username}, found {len(all_tweets)} tweets")
            
            # Wait for any pending downloads to complete
            if any(not task.done() for task in self.running_tasks):
                download_count = sum(1 for t in self.running_tasks if not t.done())
                self.log_gui(f"Waiting for {download_count} pending downloads to complete...")
                
                # Create a task to periodically update the status
                pending_tasks = [t for t in self.running_tasks if not t.done()]
                
                # Wait for all downloads or until timeout (5 minutes)
                try:
                    await asyncio.wait_for(asyncio.gather(*pending_tasks), timeout=300)
                    self.log_gui("All downloads completed")
                except asyncio.TimeoutError:
                    self.log_gui("Timed out waiting for some downloads to complete")
                    # Cancel any remaining downloads
                    for task in self.running_tasks:
                        if not task.done():
                            task.cancel()
            
            # Save final media statistics
            stats_file = output_dir / f"{username}_media_stats.json"
            # Simple stats
            media_stats["tweet_count"] = len(all_tweets)
            media_stats["processed_tweets"] = sum(1 for t in all_tweets if t.get("images_processed", False))
            media_stats["downloaded_images"] = status.images_downloaded
            media_stats["completed_at"] = time.time()
            
            # Save statistics
            atomic_write_json(media_stats, stats_file, self.log_gui)
            
            # Simple summary
            self.log_gui(f"Done with @{username}: {media_stats['tweet_count']} tweets, {media_stats['total_tweets_with_media']} with media")
            self.log_gui(f"Downloaded {status.images_downloaded} images, {media_stats.get('error_count', 0)} errors")
            
            status.complete()
            self.log_gui(f"Completed crawl for @{username}")
        except Exception as e:
            status.fail(str(e))
            self.log_gui(f"Failed crawl for @{username}: {e}")
            import traceback
            self.log_gui(traceback.format_exc())
        finally:
            self.update_status_widget(username)
            

    async def retry_failed_downloads(
        self,
        username: str,
        output_dir: Path,
        status: TwitterCrawlStatus,
        media_stats: Dict,
        all_tweets: List[Dict]
    ) -> None:
        """
        Check for failed downloads that need to be retried.
        
        Args:
            username: Twitter username
            output_dir: Directory to save media files
            status: Status object to update
            media_stats: Statistics dictionary to update
            all_tweets: List of all tweets for this user
        """
        retry_count = 0
        
        # Iterate through all tweets that have been attempted
        for tweet_id, url_attempts in status.tweet_id_to_url_attempts.items():
            # For each URL in this tweet
            for url, attempts in url_attempts.items():
                # Check if this URL has failed but hasn't exceeded max attempts
                success = status.tweet_id_to_url_success.get(tweet_id, {}).get(url, False)
                if not success and attempts < 3:  # Max 3 attempts
                    # Determine filename from URL
                    file_ext = "jpg"  # Default
                    if "." in url:
                        url_ext = url.split(".")[-1].lower()
                        if url_ext in ["jpg", "jpeg", "png", "gif", "webp"]:
                            file_ext = url_ext
                    
                    # Find index of this URL in the tweet's media items
                    j = 0
                    for tweet in all_tweets:
                        if (tweet.get("id_str") == tweet_id or tweet.get("id") == tweet_id):
                            # Get all media URLs for this tweet
                            tweet_urls = []
                            
                            # Check entities.media
                            entities = tweet.get("entities", {})
                            if entities and "media" in entities:
                                for media_item in entities.get("media", []):
                                    media_url = (media_item.get("media_url_https") or 
                                                media_item.get("media_url") or 
                                                media_item.get("expanded_url"))
                                    if media_url:
                                        tweet_urls.append(media_url)
                            
                            # Check extended_entities.media
                            extended_entities = tweet.get("extended_entities", {})
                            if extended_entities and "media" in extended_entities:
                                for media_item in extended_entities.get("media", []):
                                    media_url = (media_item.get("media_url_https") or 
                                                media_item.get("media_url") or 
                                                media_item.get("expanded_url"))
                                    if media_url:
                                        tweet_urls.append(media_url)
                            
                            # Get index of this URL in the tweet's media items
                            if url in tweet_urls:
                                j = tweet_urls.index(url)
                            break
                    
                    # Generate image path
                    image_path = output_dir / f"{tweet_id}_{j}.{file_ext}"
                    
                    # Skip if already downloaded
                    if image_path.exists():
                        status.tweet_id_to_url_success[tweet_id][url] = True
                        status.images_downloaded += 1
                        self.log_gui(f"Found already downloaded image for {url}, marking as success")
                        continue
                    
                    # Log retry
                    self.log_gui(f"Retrying download for {url} (attempt {attempts+1})")
                    
                    # Increment attempt counter
                    status.tweet_id_to_url_attempts[tweet_id][url] = attempts + 1
                    
                    # Create a background task for downloading
                    download_task = asyncio.create_task(
                        self._download_image_task(url, image_path, tweet_id, username, status, media_stats)
                    )
                    # Store the task to prevent it from being garbage collected
                    self.running_tasks.append(download_task)
                    retry_count += 1
        
        if retry_count > 0:
            self.log_gui(f"Initiated {retry_count} download retries for @{username}")
        else:
            self.log_gui(f"No failed downloads to retry for @{username}")
    
    async def process_tweets_for_media(
        self, 
        username: str, 
        tweets: List[Dict], 
        output_dir: Path, 
        status: TwitterCrawlStatus, 
        media_stats: Dict, 
        data_file: Path,
        processed_tweet_ids: set
    ) -> None:
        """
        Process a list of tweets to extract and download media.
        
        Args:
            username: Twitter username
            tweets: List of tweets to process
            output_dir: Directory to save media files
            status: Status object to update
            media_stats: Statistics dictionary to update
            data_file: Path to the data file for saving progress
            processed_tweet_ids: Set of tweet IDs that have already been processed
        """
        self.log_gui(f"Processing {len(tweets)} tweets for @{username} to extract media")
        tweets_with_media = 0
        
        for i, tweet in enumerate(tweets):
            # Skip already processed tweets
            tweet_id = tweet.get("id_str") or tweet.get("id", "")
            if tweet_id in processed_tweet_ids or tweet.get("images_processed", False):
                continue
                
            tweet_text = tweet.get("full_text") or tweet.get("text", "")
            truncated_text = (tweet_text[:50] + "...") if tweet_text and len(tweet_text) > 50 else tweet_text
            
            # Debug the entire tweet structure for the first tweet in first batch
            if media_stats["processed_tweets"] == 0 and i == 0:
                self.log_gui(f"First tweet structure keys: {tweet.keys()}")
            
            # Get media from all possible sources
            media_items = []
            
            # 1. Check entities.media
            entities = tweet.get("entities", {})
            if entities and "media" in entities:
                media_items.extend(entities.get("media", []))
            
            # 2. Check extended_entities.media (often contains videos and multiple images)
            extended_entities = tweet.get("extended_entities", {})
            if extended_entities and "media" in extended_entities:
                extended_media = extended_entities.get("media", [])
                if media_stats["processed_tweets"] == 0 and i == 0 and extended_media:
                    self.log_gui(f"Found extended_entities media: {len(extended_media)} items")
                media_items.extend(extended_media)
            
            # 3. Check for image URLs in entities.urls
            urls = entities.get("urls", [])
            for url_obj in urls:
                expanded_url = url_obj.get("expanded_url", "")
                if expanded_url and any(img_ext in expanded_url.lower() for img_ext in ['.jpg', '.jpeg', '.png', '.gif']):
                    if media_stats["processed_tweets"] == 0 and i == 0:
                        self.log_gui(f"Found image URL in expanded_url: {expanded_url}")
                    media_items.append({
                        "media_url": expanded_url,
                        "type": "photo"
                    })
            
            if media_items:
                tweets_with_media += 1
                media_stats["total_tweets_with_media"] += 1
                media_stats["total_media_items"] += len(media_items)
                
                self.log_gui(f"Found {len(media_items)} media items in tweet {tweet_id}")
                status.images_found += len(media_items)
                self.update_status_widget(username)

                # Initialize attempt tracking for this tweet if not already present
                if tweet_id not in status.tweet_id_to_url_attempts:
                    status.tweet_id_to_url_attempts[tweet_id] = {}
                if tweet_id not in status.tweet_id_to_url_success:
                    status.tweet_id_to_url_success[tweet_id] = {}

                for j, media_item in enumerate(media_items):
                    # Track media types
                    media_type = media_item.get("type", "unknown")
                    media_stats["media_types"][media_type] = media_stats["media_types"].get(media_type, 0) + 1
                    
                    # Try different fields where the URL might be
                    media_url = (media_item.get("media_url_https") or 
                                media_item.get("media_url") or 
                                media_item.get("expanded_url"))
                    
                    if media_url:
                        # Use the tweet ID and a counter to generate unique filenames
                        file_ext = "jpg"  # Default
                        if "." in media_url:
                            url_ext = media_url.split(".")[-1].lower()
                            if url_ext in ["jpg", "jpeg", "png", "gif", "webp"]:
                                file_ext = url_ext
                                
                        image_path = output_dir / f"{tweet_id}_{j}.{file_ext}"
                        
                        # Check if the file already exists
                        if image_path.exists():
                            # Count already downloaded media
                            status.images_downloaded += 1
                            status.tweet_id_to_url_success[tweet_id][media_url] = True
                            self.update_status_widget(username)
                        else:
                            # Check if we've attempted this URL before
                            attempts = status.tweet_id_to_url_attempts[tweet_id].get(media_url, 0)
                            success = status.tweet_id_to_url_success[tweet_id].get(media_url, False)
                            
                            # Only attempt download if we haven't succeeded yet and haven't exceeded max attempts
                            if not success and attempts < 3:  # Max 3 attempts
                                # Increment attempt counter
                                status.tweet_id_to_url_attempts[tweet_id][media_url] = attempts + 1
                                
                                # Create a background task for downloading
                                # This allows tweet fetching to continue without waiting for downloads
                                download_task = asyncio.create_task(
                                    self._download_image_task(media_url, image_path, tweet_id, username, status, media_stats)
                                )
                                # Store the task to prevent it from being garbage collected
                                self.running_tasks.append(download_task)
            
            # Mark this tweet as having been PROCESSED for images (not necessarily downloaded)
            # We're separating the concept of "processed" from "downloaded successfully"
            # A tweet is "processed" once we've identified its media and initiated downloads
            tweet["images_processed"] = True
            processed_tweet_ids.add(tweet_id)
            media_stats["processed_tweets"] += 1
            
            # Log progress regularly
            if i % 10 == 0 or i == len(tweets) - 1:
                self.log_gui(f"Processed tweet {i+1}/{len(tweets)} for @{username}")
                
        if tweets_with_media > 0:
            self.log_gui(f"Found media in {tweets_with_media} out of {len(tweets)} tweets")
    
    async def _download_image_task(
        self, url: str, path: Path, tweet_id: str, username: str, 
        status: TwitterCrawlStatus, media_stats: Dict
    ) -> None:
        """
        Background task for downloading an image.
        This runs independently of the tweet processing loop.
        
        Args:
            url: The URL to download from
            path: The local path to save the file to
            tweet_id: ID of the tweet this image belongs to
            username: Username of the account
            status: Status object to update
            media_stats: Statistics dictionary to update
        """
        try:
            await self.download_image(url, path)
            
            # Update status after successful download
            status.images_downloaded += 1
            # Mark as successfully downloaded
            status.tweet_id_to_url_success[tweet_id][url] = True
            self.update_status_widget(username)
            
            self.log_gui(f"Successfully downloaded {url} for tweet {tweet_id} on attempt " 
                         f"{status.tweet_id_to_url_attempts[tweet_id].get(url, 0)}")
            
        except Exception as e:
            # Count error
            if "error_count" not in media_stats:
                media_stats["error_count"] = 0
            media_stats["error_count"] += 1
            
            # Log the error
            self.log_gui(f"Failed to download {url} for tweet {tweet_id} (attempt "
                         f"{status.tweet_id_to_url_attempts[tweet_id].get(url, 0)}): {str(e)}")
            
            # Mark as not successful
            status.tweet_id_to_url_success[tweet_id][url] = False
        
        # Remove this task from running_tasks when done
        for task in self.running_tasks[:]:
            if task.done():
                self.running_tasks.remove(task)
    
    async def download_image(self, url: str, path: Path) -> None:
        """
        Download an image or other media file from a URL.
        Uses a separate session with limited connections.
        
        Args:
            url: The URL to download from (absolute URL)
            path: The local path to save the file to
        """
        try:
            # Simple User-Agent to avoid blocks
            headers = {"User-Agent": "Mozilla/5.0"}
            
            # Log the URL we're trying to download
            self.log_gui(f"Downloading media from: {url}")
            
            # Use the dedicated image session with the full URL
            # The connection limiting is handled by the TCPConnector
            async with self.image_session.get(url, headers=headers, timeout=30) as response:
                if response.status == 200:
                    # Create parent directories if needed
                    path.parent.mkdir(parents=True, exist_ok=True)
                    
                    # Direct write to file - simple is better
                    content = await response.read()
                    with open(path, "wb") as f:
                        f.write(content)
                    
                    self.log_gui(f"Successfully saved {len(content)} bytes to {path.name}")
                else:
                    raise Exception(f"HTTP error {response.status}")
                    
        except Exception as e:
            # Use traceback for detailed error info
            import traceback
            self.log_gui(f"Error downloading {url}:")
            self.log_gui(traceback.format_exc())
            raise  # Re-raise the original exception


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
