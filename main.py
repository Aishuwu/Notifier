import os
import re
import nextcord
from nextcord.ext import commands, tasks
from googleapiclient.discovery import build

intents = nextcord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
if not YOUTUBE_API_KEY:
    print("Error: YOUTUBE_API_KEY is missing!")
youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

tracked_channels = {}
last_live_streams = {}

# Function to extract video/short details
def check_video_uploads(channel_id):
    try:
        request = youtube.search().list(
            part="snippet",
            channelId=channel_id,
            type="video",  # Check for all videos (including live streams, shorts, and uploads)
            maxResults=1,
            order="date"  # Get the most recent video
        )
        response = request.execute()
        print(f"Response from YouTube API for channel {channel_id}: {response}")

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

# Function to get video details (duration) for identifying shorts
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

# Function to check if the video is a short
def is_short(duration):
    # Shorts are videos less than 60 seconds long
    if duration and 'PT' in duration:
        # Check if duration is less than 60 seconds (ISO 8601 duration format)
        minutes = re.search(r'(\d+)M', duration)
        seconds = re.search(r'(\d+)S', duration)
        if not minutes and seconds and int(seconds.group(1)) <= 60:
            return True
    return False

# Function to get the channel name from ID
def get_channel_name(channel_id):
    try:
        request = youtube.channels().list(
            part="snippet",
            id=channel_id
        )
        response = request.execute()
        if 'items' in response and len(response['items']) > 0:
            channel_name = response['items'][0]['snippet']['title']
            return channel_name
        else:
            return None
    except Exception as e:
        print(f"Error fetching channel name: {e}")
        return None

@bot.slash_command(name="add_channel", description="Add a YouTube channel (by ID or username) to track for video uploads and live streams.")
async def add_channel(interaction: nextcord.Interaction, input_str: str):
    await interaction.response.defer()

    guild_id = interaction.guild.id
    channel_id = get_channel_id(input_str)

    if not channel_id:
        await interaction.followup.send(f"Error: Unable to find channel by the input '{input_str}'. Please check the channel ID or username.")
        return

    if guild_id not in tracked_channels:
        tracked_channels[guild_id] = []

    if channel_id not in tracked_channels[guild_id]:
        channel_name = get_channel_name(channel_id)
        if channel_name:
            tracked_channels[guild_id].append(channel_id)
            await interaction.followup.send(f"Now tracking YouTube channel: {channel_name}")
            print(f"Tracking channel {channel_name} (ID: {channel_id}) for guild {guild_id}")
        else:
            await interaction.followup.send("Error: Unable to retrieve the channel name. Please check the channel ID or username.")
    else:
        await interaction.followup.send(f"Channel {channel_id} is already being tracked.")

@bot.slash_command(name="remove_channel", description="Remove a YouTube channel from tracking.")
async def remove_channel(interaction: nextcord.Interaction):
    await interaction.response.defer()

    guild_id = interaction.guild.id

    if guild_id not in tracked_channels or len(tracked_channels[guild_id]) == 0:
        await interaction.followup.send("No channels are currently being tracked.")
        return

    options = []
    for channel_id in tracked_channels[guild_id]:
        channel_name = get_channel_name(channel_id)
        if channel_name:
            options.append(nextcord.SelectOption(label=channel_name, value=channel_id))
        else:
            options.append(nextcord.SelectOption(label=f"Unknown Channel (ID: {channel_id})", value=channel_id))

    class ChannelSelect(nextcord.ui.Select):
        def __init__(self):
            super().__init__(
                placeholder="Select a channel to remove...",
                min_values=1,
                max_values=1,
                options=options
            )

        async def callback(self, interaction: nextcord.Interaction):
            selected_channel_id = self.values[0]
            tracked_channels[guild_id].remove(selected_channel_id)
            last_live_streams.pop(selected_channel_id, None)
            channel_name = get_channel_name(selected_channel_id)
            await interaction.followup.send(f"Removed YouTube channel: {channel_name or 'Unknown Channel'}")
            print(f"Removed channel {channel_name or selected_channel_id} for guild {guild_id}")

    view = nextcord.ui.View()
    view.add_item(ChannelSelect())
    await interaction.followup.send("Select a channel to remove:", view=view)

@bot.slash_command(name="list_channels", description="List all YouTube channels being tracked.")
async def list_channels(interaction: nextcord.Interaction):
    await interaction.response.defer()

    guild_id = interaction.guild.id

    if guild_id in tracked_channels and len(tracked_channels[guild_id]) > 0:
        channel_names = []
        for channel_id in tracked_channels[guild_id]:
            channel_name = get_channel_name(channel_id)
            if channel_name:
                channel_names.append(channel_name)
            else:
                channel_names.append(f"Unknown Channel (ID: {channel_id})")

        channels_list = "\n".join(channel_names)
        await interaction.followup.send(f"Currently tracking these channels:\n{channels_list}")
    else:
        await interaction.followup.send("No channels are currently being tracked.")

# Task to check for video uploads and live streams periodically
@tasks.loop(minutes=3)
async def check_streams():
    print("Checking for new uploads and live streams...")
    for guild_id, channels in tracked_channels.items():
        for channel_id in channels:
            print(f"Checking channel {channel_id} for guild {guild_id}")
            is_video, video_title, video_thumbnail, video_url, video_id = check_video_uploads(channel_id)

            if not is_video:
                last_live_streams[channel_id] = None
                continue

            if last_live_streams.get(channel_id) == video_id:
                continue  # Skip if the video has already been notified

            guild = bot.get_guild(guild_id)
            if guild and is_video:
                last_live_streams[channel_id] = video_id

                # Check if the video is a short or regular video
                video_duration = check_video_details(video_id)
                if is_short(video_duration):
                    title_prefix = "New Short Uploaded"
                    embed_color = nextcord.Color.green()
                else:
                    title_prefix = "New Video Uploaded"
                    embed_color = nextcord.Color.blue()

                # Create the embed message for the video/short
                embed = nextcord.Embed(
                    title=f"{title_prefix}: {video_title}",
                    description=f"[Click to watch the video]({video_url})",
                    color=embed_color
                )
                embed.set_image(url=video_thumbnail)

                # Send the notification to the first text channel in the guild
                channel = guild.text_channels[0]
                await channel.send(content="@everyone", embed=embed)

@bot.slash_command(name="ping", description="Ping the bot to check if it's online.")
async def ping(interaction: nextcord.Interaction):
    await interaction.response.send_message("Pong!")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    await bot.change_presence(activity=nextcord.Activity(type=nextcord.ActivityType.watching, name="YouTube streams"))
    try:
        await bot.sync_application_commands()
    except Exception as e:
        print(f"Error syncing slash commands: {e}")
    check_streams.start()

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not DISCORD_BOT_TOKEN:
    print("Error: DISCORD_BOT_TOKEN is missing!")
else:
    bot.run(DISCORD_BOT_TOKEN)
