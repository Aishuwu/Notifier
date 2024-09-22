import os
import re
import nextcord
import requests
from nextcord.ext import commands, tasks
from googleapiclient.discovery import build

intents = nextcord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# YouTube API setup
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
if not YOUTUBE_API_KEY:
    print("Error: YOUTUBE_API_KEY is missing!")
youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

# Twitch API setup
TWITCH_CLIENT_ID = os.getenv('TWITCH_CLIENT_ID')
TWITCH_CLIENT_SECRET = os.getenv('TWITCH_CLIENT_SECRET')
TWITCH_OAUTH_TOKEN = None

tracked_channels = {'youtube': {}, 'twitch': {}}
notification_channels = {}  # Custom notification channels per guild
tracked_types = {}  # Store content type tracking preferences
last_live_streams = {}
notification_messages = {}  # Store custom notification messages

# Function to get Twitch OAuth token
def get_twitch_oauth_token():
    global TWITCH_OAUTH_TOKEN
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        'client_id': TWITCH_CLIENT_ID,
        'client_secret': TWITCH_CLIENT_SECRET,
        'grant_type': 'client_credentials'
    }
    response = requests.post(url, params=params)
    data = response.json()
    TWITCH_OAUTH_TOKEN = data['access_token']

# Function to check if Twitch stream is live
def check_twitch_stream(channel_name):
    global TWITCH_OAUTH_TOKEN
    url = f"https://api.twitch.tv/helix/streams"
    headers = {
        'Client-ID': TWITCH_CLIENT_ID,
        'Authorization': f'Bearer {TWITCH_OAUTH_TOKEN}'
    }
    params = {'user_login': channel_name}

    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 401:  # If token expired, refresh it
        get_twitch_oauth_token()
        headers['Authorization'] = f'Bearer {TWITCH_OAUTH_TOKEN}'
        response = requests.get(url, headers=headers, params=params)

    data = response.json()
    if 'data' in data and len(data['data']) > 0:
        stream_data = data['data'][0]
        stream_title = stream_data['title']
        stream_thumbnail = stream_data['thumbnail_url'].replace('{width}', '1280').replace('{height}', '720')
        stream_url = f"https://www.twitch.tv/{channel_name}"
        return (True, stream_title, stream_thumbnail, stream_url)
    return (False, None, None, None)

# Function to check YouTube video uploads (including shorts)
def check_video_uploads(channel_id):
    try:
        request = youtube.search().list(
            part="snippet",
            channelId=channel_id,
            type="video",
            maxResults=1,
            order="date"
        )
        response = request.execute()
        if 'items' in response and len(response['items']) > 0:
            video = response['items'][0]
            video_id = video['id']['videoId']
            video_title = video['snippet']['title']
            video_thumbnail = video['snippet']['thumbnails']['high']['url']
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            return (True, video_title, video_thumbnail, video_url, video_id)
    except Exception as e:
        print(f"Error fetching video upload data: {e}")
    return (False, None, None, None, None)

# Function to check if YouTube video is a short
def check_video_details(video_id):
    try:
        request = youtube.videos().list(
            part="contentDetails",
            id=video_id
        )
        response = request.execute()
        if 'items' in response and len(response['items']) > 0:
            duration = response['items'][0]['contentDetails']['duration']
            return duration
    except Exception as e:
        print(f"Error fetching video details: {e}")
    return None

def is_short(duration):
    if duration and 'PT' in duration:
        minutes = re.search(r'(\d+)M', duration)
        seconds = re.search(r'(\d+)S', duration)
        if not minutes and seconds and int(seconds.group(1)) <= 60:
            return True
    return False

# Set custom notification channel
@bot.slash_command(name="set_notification_channel", description="Set the channel where notifications will be sent.")
async def set_notification_channel(interaction: nextcord.Interaction, channel: nextcord.TextChannel):
    guild_id = interaction.guild.id
    notification_channels[guild_id] = channel.id
    await interaction.response.send_message(f"Notifications will now be sent to {channel.mention}")

# Custom platform selection dropdown for adding channels
class PlatformSelect(nextcord.ui.Select):
    def __init__(self):
        options = [
            nextcord.SelectOption(label="YouTube", description="Track YouTube channel", value="youtube"),
            nextcord.SelectOption(label="Twitch", description="Track Twitch channel", value="twitch")
        ]
        super().__init__(placeholder="Select the platform to track", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: nextcord.Interaction):
        await interaction.response.send_message(f"Selected platform: {self.values[0]}")

# Slash command to add a YouTube or Twitch channel with platform selection
@bot.slash_command(name="add_channel", description="Add a YouTube or Twitch channel to track.")
async def add_channel(interaction: nextcord.Interaction, channel_name: str, content_type: str = 'all'):
    await interaction.response.defer()

    # Add platform selection dropdown to interaction
    view = nextcord.ui.View()
    platform_select = PlatformSelect()
    view.add_item(platform_select)
    
    await interaction.followup.send("Please select the platform:", view=view)
    
    platform = platform_select.values[0].lower()
    guild_id = interaction.guild.id

    try:
        # Handle YouTube channel
        if platform == 'youtube':
            channel_id = get_channel_id(channel_name)
            if not channel_id:
                await interaction.followup.send(f"Error: Unable to find YouTube channel '{channel_name}'.")
                return
            tracked_channels['youtube'].setdefault(guild_id, []).append(channel_id)
            tracked_types[channel_id] = content_type.lower()
            await interaction.followup.send(f"Now tracking YouTube channel: {channel_name} on YouTube for {content_type}")

        # Handle Twitch channel
        elif platform == 'twitch':
            tracked_channels['twitch'].setdefault(guild_id, []).append(channel_name.lower())
            await interaction.followup.send(f"Now tracking Twitch channel: {channel_name} on Twitch")

        # Invalid platform (this shouldn't occur with dropdown, but just in case)
        else:
            await interaction.followup.send("Invalid platform. Please use 'youtube' or 'twitch'.")

    except Exception as e:
        print(f"Error in add_channel: {e}")
        await interaction.followup.send("An error occurred while adding the channel. Please try again.")

# Slash command to get YouTube channel statistics
@bot.slash_command(name="channel_stats", description="Get statistics for a YouTube channel.")
async def channel_stats(interaction: nextcord.Interaction, channel_name: str):
    await interaction.response.defer()
    channel_id = get_channel_id(channel_name)
    if not channel_id:
        await interaction.followup.send(f"Channel {channel_name} not found.")
        return

    request = youtube.channels().list(part="statistics", id=channel_id)
    response = request.execute()
    if 'items' in response and len(response['items']) > 0:
        stats = response['items'][0]['statistics']
        subs = stats['subscriberCount']
        views = stats['viewCount']
        videos = stats['videoCount']
        await interaction.followup.send(f"{channel_name} has {subs} subscribers, {views} views, and {videos} videos.")

# Slash command to list tracked YouTube and Twitch channels
@bot.slash_command(name="list_channels", description="List all YouTube and Twitch channels being tracked.")
async def list_channels(interaction: nextcord.Interaction):
    guild_id = interaction.guild.id
    tracked_youtube = tracked_channels.get('youtube', {}).get(guild_id, [])
    tracked_twitch = tracked_channels.get('twitch', {}).get(guild_id, [])

    if not tracked_youtube and not tracked_twitch:
        await interaction.response.send_message("No channels are currently being tracked.")
        return

    youtube_channels = "\n".join(tracked_youtube) if tracked_youtube else "None"
    twitch_channels = "\n".join(tracked_twitch) if tracked_twitch else "None"
    await interaction.response.send_message(f"**YouTube Channels:**\n{youtube_channels}\n\n**Twitch Channels:**\n{twitch_channels}")

# Slash command to remove a YouTube or Twitch channel
@bot.slash_command(name="remove_channel", description="Remove a YouTube or Twitch channel from tracking.")
async def remove_channel(interaction: nextcord.Interaction, platform: str, channel_name: str):
    await interaction.response.defer()

    guild_id = interaction.guild.id
    platform = platform.lower()

    if platform == 'youtube' and guild_id in tracked_channels['youtube']:
        if channel_name in tracked_channels['youtube'][guild_id]:
            tracked_channels['youtube'][guild_id].remove(channel_name)
            await interaction.followup.send(f"Removed YouTube channel: {channel_name}")
        else:
            await interaction.followup.send(f"Channel {channel_name} is not being tracked.")

    elif platform == 'twitch' and guild_id in tracked_channels['twitch']:
        if channel_name.lower() in tracked_channels['twitch'][guild_id]:
            tracked_channels['twitch'][guild_id].remove(channel_name.lower())
            await interaction.followup.send(f"Removed Twitch channel: {channel_name}")
        else:
            await interaction.followup.send(f"Channel {channel_name} is not being tracked.")
    
    else:
        await interaction.followup.send(f"No {platform} channel named '{channel_name}' found in tracking.")

# Command to customize notification messages
@bot.slash_command(name="set_notification_message", description="Customize the notification message.")
async def set_notification_message(interaction: nextcord.Interaction, message: str):
    guild_id = interaction.guild.id
    notification_messages[guild_id] = message
    await interaction.followup.send("Notification message updated!")

# Interactive Poll for Live Streams
class Poll(nextcord.ui.View):
    def __init__(self):
        super().__init__()
        self.value = None

    @nextcord.ui.button(label="Yes", style=nextcord.ButtonStyle.green)
    async def yes(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await interaction.response.send_message("You voted Yes!")

    @nextcord.ui.button(label="No", style=nextcord.ButtonStyle.red)
    async def no(self, button: nextcord.ui.Button, interaction: nextcord.Interaction):
        await interaction.response.send_message("You voted No!")

@bot.slash_command(name="poll", description="Create a poll for a live stream.")
async def poll(interaction: nextcord.Interaction):
    view = Poll()
    await interaction.response.send_message("Are you watching the stream?", view=view)

# Task to check for video uploads, live streams, and Twitch streams
@tasks.loop(minutes=3)
async def check_streams():
    print("Checking for new YouTube uploads, live streams, and Twitch streams...")

    for guild_id, channels in tracked_channels['youtube'].items():
        for channel_id in channels:
            is_video, video_title, video_thumbnail, video_url, video_id = check_video_uploads(channel_id)

            if not is_video:
                last_live_streams[channel_id] = None
                continue

            if last_live_streams.get(channel_id) == video_id:
                continue  # Skip if the video has already been notified

            guild = bot.get_guild(guild_id)
            if guild and is_video:
                last_live_streams[channel_id] = video_id

                video_duration = check_video_details(video_id)
                if is_short(video_duration):
                    title_prefix = "New Short Uploaded"
                    embed_color = nextcord.Color.green()
                else:
                    title_prefix = "New Video Uploaded"
                    embed_color = nextcord.Color.blue()

                embed = nextcord.Embed(
                    title=f"{title_prefix}: {video_title}",
                    description=f"[Click to watch the video]({video_url})",
                    color=embed_color
                )
                embed.set_image(url=video_thumbnail)

                # Send notification to a custom or default channel
                channel = bot.get_channel(notification_channels.get(guild_id, guild.text_channels[0].id))
                await channel.send(content=notification_messages.get(guild_id, "@everyone"), embed=embed)

    for guild_id, channels in tracked_channels['twitch'].items():
        for channel_name in channels:
            is_live, stream_title, stream_thumbnail, stream_url = check_twitch_stream(channel_name)

            if is_live and last_live_streams.get(channel_name) != stream_url:
                last_live_streams[channel_name] = stream_url
                guild = bot.get_guild(guild_id)
                if guild:
                    embed = nextcord.Embed(
                        title=f"Twitch Stream Live: {stream_title}",
                        description=f"[Click to watch the stream]({stream_url})",
                        color=nextcord.Color.purple()
                    )
                    embed.set_image(url=stream_thumbnail)
                    channel = bot.get_channel(notification_channels.get(guild_id, guild.text_channels[0].id))
                    await channel.send(content=notification_messages.get(guild_id, "@everyone"), embed=embed)

@bot.slash_command(name="ping", description="Ping the bot to check if it's online.")
async def ping(interaction: nextcord.Interaction):
    await interaction.response.send_message("Pong!")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    await bot.change_presence(activity=nextcord.Activity(type=nextcord.ActivityType.watching, name="YouTube and Twitch streams"))
    get_twitch_oauth_token()  # Get Twitch OAuth token on startup
    check_streams.start()

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not DISCORD_BOT_TOKEN:
    print("Error: DISCORD_BOT_TOKEN is missing!")
else:
    bot.run(DISCORD_BOT_TOKEN)
