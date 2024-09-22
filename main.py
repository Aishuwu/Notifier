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

# Function to check for new YouTube video uploads
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

# Function to get video details (such as duration) from YouTube
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

# Function to check if a video is a short (less than 60 seconds)
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

# Slash command to add a YouTube or Twitch channel
@bot.slash_command(name="add_channel", description="Add a YouTube or Twitch channel to track.")
async def add_channel(interaction: nextcord.Interaction, platform: str, channel_name: str):
    await interaction.response.defer()

    guild_id = interaction.guild.id
    platform = platform.lower()

    try:
        # Handle YouTube channel
        if platform == 'youtube':
            channel_id, channel_title = get_youtube_channel(channel_name)
            if not channel_id:
                await interaction.followup.send(f"Error: Unable to find YouTube channel '{channel_name}'.")
                return
            tracked_channels['youtube'].setdefault(guild_id, []).append({'id': channel_id, 'title': channel_title})
            await interaction.followup.send(f"Now tracking YouTube channel: {channel_title}")

        # Handle Twitch channel
        elif platform == 'twitch':
            tracked_channels['twitch'].setdefault(guild_id, []).append(channel_name.lower())
            await interaction.followup.send(f"Now tracking Twitch channel: {channel_name}")

        # Invalid platform
        else:
            await interaction.followup.send("Invalid platform. Please use 'youtube' or 'twitch'.")

    except Exception as e:
        print(f"Error in add_channel: {e}")
        await interaction.followup.send("An error occurred while adding the channel. Please try again.")

# Slash command to list tracked YouTube and Twitch channels
@bot.slash_command(name="list_channels", description="List all YouTube and Twitch channels being tracked.")
async def list_channels(interaction: nextcord.Interaction):
    guild_id = interaction.guild.id
    tracked_youtube = tracked_channels.get('youtube', {}).get(guild_id, [])
    tracked_twitch = tracked_channels.get('twitch', {}).get(guild_id, [])

    youtube_list = [channel['title'] for channel in tracked_youtube]

    if not youtube_list and not tracked_twitch:
        await interaction.response.send_message("No channels are currently being tracked.")
        return

    youtube_channels = "\n".join(youtube_list) if youtube_list else "None"
    twitch_channels = "\n".join(tracked_twitch) if tracked_twitch else "None"
    await interaction.response.send_message(f"**YouTube Channels:**\n{youtube_channels}\n\n**Twitch Channels:**\n{twitch_channels}")

# Custom Select menu for removing a channel
class RemoveChannelSelect(nextcord.ui.Select):
    def __init__(self, guild_id, youtube_channels, twitch_channels):
        options = []

        for channel in youtube_channels:
            options.append(nextcord.SelectOption(label=channel['title'], description="YouTube", value=f"youtube|{channel['id']}"))

        for channel_name in twitch_channels:
            options.append(nextcord.SelectOption(label=channel_name, description="Twitch", value=f"twitch|{channel_name}"))

        super().__init__(placeholder="Select a channel to remove...", options=options)

    async def callback(self, interaction: nextcord.Interaction):
        guild_id = interaction.guild.id
        selected_value = self.values[0]
        platform, channel_id_or_name = selected_value.split("|")

        if platform == "youtube":
            tracked_channels['youtube'][guild_id] = [ch for ch in tracked_channels['youtube'][guild_id] if ch['id'] != channel_id_or_name]
            await interaction.followup.send(f"Removed YouTube channel with ID: {channel_id_or_name}")
        elif platform == "twitch":
            tracked_channels['twitch'][guild_id].remove(channel_id_or_name)
            await interaction.followup.send(f"Removed Twitch channel: {channel_id_or_name}")

# Slash command to remove a YouTube or Twitch channel using a dropdown
@bot.slash_command(name="remove_channel", description="Remove a YouTube or Twitch channel from tracking.")
async def remove_channel(interaction: nextcord.Interaction):
    guild_id = interaction.guild.id

    youtube_channels = tracked_channels.get('youtube', {}).get(guild_id, [])
    twitch_channels = tracked_channels.get('twitch', {}).get(guild_id, [])

    if not youtube_channels and not twitch_channels:
        await interaction.response.send_message("No channels are currently being tracked.")
        return

    view = nextcord.ui.View()
    view.add_item(RemoveChannelSelect(guild_id, youtube_channels, twitch_channels))

    await interaction.response.send_message("Select the channel you want to remove:", view=view)

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
        for channel in channels:
            channel_id = channel['id']
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
