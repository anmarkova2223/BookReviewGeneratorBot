import os
import asyncio
import logging
from datetime import datetime
from typing import Optional, List
import tempfile
import io

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, filters, ContextTypes
)
from pymongo import MongoClient
from bson import ObjectId
import openai

from dotenv import load_dotenv
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class BookNotesBot:
    def __init__(self):
        # Initialize API keys from environment variables
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        self.mongodb_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
        
        # Initialize OpenAI
        openai.api_key = self.openai_api_key
        
        # Initialize MongoDB
        self.client = MongoClient(self.mongodb_uri)
        self.db = self.client.book_notes
        self.books_collection = self.db.books
        self.users_collection = self.db.users
        
        # Initialize Telegram bot
        self.application = Application.builder().token(self.telegram_token).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        """Set up all command and message handlers"""
        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("newbook", self.new_book_command))
        self.application.add_handler(CommandHandler("mybooks", self.my_books_command))
        self.application.add_handler(CommandHandler("currentbook", self.current_book_command))
        self.application.add_handler(CommandHandler("switchbook", self.switch_book_command))
        self.application.add_handler(CommandHandler("review", self.generate_review_command))
        self.application.add_handler(CommandHandler("finish", self.finish_book_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        
        # Message handlers
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_note))
        self.application.add_handler(MessageHandler(filters.VOICE, self.handle_voice_note))
        
        # Callback query handler for inline keyboards
        self.application.add_handler(CallbackQueryHandler(self.handle_callback_query))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Welcome message and setup user"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Anonymous"
        
        # Create or update user in database
        self.users_collection.update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "username": username,
                    "last_active": datetime.utcnow()
                },
                "$setOnInsert": {
                    "created_at": datetime.utcnow(),
                    "current_book_id": None
                }
            },
            upsert=True
        )
        
        welcome_text = """
📚 **Welcome to BookNotes Bot!**

I help you track your reading notes and generate AI-powered reviews.

**Quick Start:**
• `/newbook <title>` - Start tracking a new book
• Send me text or voice messages - I'll save them as notes
• `/review` - Generate a review when you're done reading

**Commands:**
• `/mybooks` - See all your books
• `/currentbook` - Show current book
• `/switchbook` - Change active book  
• `/finish` - Mark current book as finished
• `/stats` - Your reading statistics
• `/help` - Show this help

Start by creating your first book with `/newbook <title>`!
        """
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help information"""
        help_text = """
📖 **BookNotes Bot Help**

**Commands:**
• `/newbook <title>` - Start a new book
• `/mybooks` - List all your books
• `/currentbook` - Show current active book
• `/switchbook` - Switch to different book
• `/review` - Generate AI review of current book
• `/finish` - Mark book as finished
• `/stats` - Show your reading statistics

**Usage:**
1. Create a book: `/newbook The Great Gatsby`
2. Send notes as text or voice messages
3. Generate review: `/review`

**Tips:**
• Voice messages are automatically transcribed
• All notes are saved to your current book
• You can switch between multiple books
• Reviews are generated using AI based on your notes
        """
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def new_book_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Create a new book"""
        user_id = update.effective_user.id
        
        if not context.args:
            await update.message.reply_text(
                "Please provide a book title: `/newbook The Great Gatsby`",
                parse_mode='Markdown'
            )
            return
        
        title = " ".join(context.args)
        
        # Create new book document
        book_doc = {
            "user_id": user_id,
            "title": title,
            "notes": [],
            "status": "reading",
            "created_at": datetime.utcnow(),
            "finished_at": None
        }
        
        # Insert book
        result = self.books_collection.insert_one(book_doc)
        book_id = result.inserted_id
        
        # Set as user's current book
        self.users_collection.update_one(
            {"user_id": user_id},
            {"$set": {"current_book_id": book_id}}
        )
        
        await update.message.reply_text(
            f"📖 Started tracking **{title}**\n\n"
            f"This is now your active book. Send me notes and I'll save them!",
            parse_mode='Markdown'
        )
    
    async def my_books_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List all user's books"""
        user_id = update.effective_user.id
        
        books = list(self.books_collection.find(
            {"user_id": user_id}
        ).sort("created_at", -1))
        
        if not books:
            await update.message.reply_text(
                "📚 You haven't started any books yet!\n\n"
                "Use `/newbook <title>` to get started."
            )
            return
        
        # Get current book ID
        user = self.users_collection.find_one({"user_id": user_id})
        current_book_id = user.get("current_book_id") if user else None
        
        books_text = "📚 **Your Books:**\n\n"
        for book in books:
            status_emoji = "📖" if book["status"] == "reading" else "✅"
            current_marker = " 🔸" if book["_id"] == current_book_id else ""
            note_count = len(book["notes"])
            
            books_text += (
                f"{status_emoji} **{book['title']}**{current_marker}\n"
                f"   • {note_count} notes • {book['status']}\n\n"
            )
        
        await update.message.reply_text(books_text, parse_mode='Markdown')
    
    async def current_book_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show current active book"""
        user_id = update.effective_user.id
        current_book = self.get_current_book(user_id)
        
        if not current_book:
            await update.message.reply_text(
                "📚 No active book set.\n\n"
                "Use `/newbook <title>` to start tracking a book!"
            )
            return
        
        note_count = len(current_book["notes"])
        status = current_book["status"]
        created_date = current_book["created_at"].strftime("%B %d, %Y")
        
        current_text = (
            f"📖 **Current Book:** {current_book['title']}\n\n"
            f"📊 **Stats:**\n"
            f"• {note_count} notes saved\n"
            f"• Status: {status}\n"
            f"• Started: {created_date}\n\n"
            f"Send me text or voice messages to add notes!"
        )
        
        await update.message.reply_text(current_text, parse_mode='Markdown')
    
    async def switch_book_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show books to switch to"""
        user_id = update.effective_user.id
        
        books = list(self.books_collection.find(
            {"user_id": user_id, "status": "reading"}
        ).sort("created_at", -1))
        
        if not books:
            await update.message.reply_text(
                "📚 No active books found.\n\n"
                "Use `/newbook <title>` to start a new book!"
            )
            return
        
        if len(books) == 1:
            await update.message.reply_text(
                f"📖 You only have one active book: **{books[0]['title']}**",
                parse_mode='Markdown'
            )
            return
        
        # Create inline keyboard with book options
        keyboard = []
        for book in books:
            note_count = len(book["notes"])
            button_text = f"{book['title']} ({note_count} notes)"
            callback_data = f"switch_{book['_id']}"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "📚 **Select a book to switch to:**",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard callbacks"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        if query.data.startswith("switch_"):
            book_id = ObjectId(query.data.split("_")[1])
            
            # Update user's current book
            self.users_collection.update_one(
                {"user_id": user_id},
                {"$set": {"current_book_id": book_id}}
            )
            
            # Get book title
            book = self.books_collection.find_one({"_id": book_id})
            
            await query.edit_message_text(
                f"📖 Switched to **{book['title']}**\n\n"
                f"Send me notes and I'll save them to this book!",
                parse_mode='Markdown'
            )
    
    async def handle_text_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text notes"""
        user_id = update.effective_user.id
        text_content = update.message.text
        
        current_book = self.get_current_book(user_id)
        if not current_book:
            await update.message.reply_text(
                "📚 No active book set! Use `/newbook <title>` to start tracking a book."
            )
            return
        
        # Add note to current book
        note = {
            "content": text_content,
            "type": "text",
            "timestamp": datetime.utcnow(),
            "message_id": update.message.message_id
        }
        
        self.books_collection.update_one(
            {"_id": current_book["_id"]},
            {"$push": {"notes": note}}
        )
        
        # Confirm note saved
        await update.message.reply_text(
            f"✅ Note saved to **{current_book['title']}**",
            parse_mode='Markdown'
        )
    
    async def handle_voice_note(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle voice messages"""
        user_id = update.effective_user.id
        
        current_book = self.get_current_book(user_id)
        if not current_book:
            await update.message.reply_text(
                "📚 No active book set! Use `/newbook <title>` to start tracking a book."
            )
            return
        
        # Send processing message
        processing_msg = await update.message.reply_text("🎤 Processing voice note...")
        
        try:
            # Download voice file
            voice_file = await update.message.voice.get_file()
            voice_bytes = await voice_file.download_as_bytearray()
            
            # Create temporary file for OpenAI Whisper
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
                temp_file.write(voice_bytes)
                temp_file_path = temp_file.name
            
            # Transcribe using OpenAI Whisper
            with open(temp_file_path, "rb") as audio_file:
                transcript = openai.Audio.transcribe("whisper-1", audio_file)
            
            # Clean up temporary file
            os.unlink(temp_file_path)
            
            transcribed_text = transcript.text
            
            # Add note to current book
            note = {
                "content": transcribed_text,
                "type": "voice",
                "timestamp": datetime.utcnow(),
                "message_id": update.message.message_id,
                "duration": update.message.voice.duration
            }
            
            self.books_collection.update_one(
                {"_id": current_book["_id"]},
                {"$push": {"notes": note}}
            )
            
            # Update processing message with result
            await processing_msg.edit_text(
                f"✅ Voice note transcribed and saved to **{current_book['title']}**\n\n"
                f"📝 **Transcription:** {transcribed_text}",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Error processing voice note: {e}")
            await processing_msg.edit_text(
                "❌ Sorry, I couldn't process your voice note. Please try again."
            )
    
    async def generate_review_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Generate AI review of current book"""
        user_id = update.effective_user.id
        
        current_book = self.get_current_book(user_id)
        if not current_book:
            await update.message.reply_text(
                "📚 No active book set! Use `/newbook <title>` to start tracking a book."
            )
            return
        
        if not current_book["notes"]:
            await update.message.reply_text(
                f"📖 No notes found for **{current_book['title']}**\n\n"
                f"Add some notes first, then I can generate a review!",
                parse_mode='Markdown'
            )
            return
        
        # Send processing message
        processing_msg = await update.message.reply_text(
            f"🤖 Generating AI review for **{current_book['title']}**...\n"
            f"📝 Analyzing {len(current_book['notes'])} notes...",
            parse_mode='Markdown'
        )
        
        try:
            # Prepare notes for AI
            notes_text = "\n\n".join([
                f"Note {i+1}: {note['content']}" 
                for i, note in enumerate(current_book['notes'])
            ])
            
            # Generate review using OpenAI
            review = await self.generate_ai_review(current_book['title'], notes_text)
            
            # Save review to book
            self.books_collection.update_one(
                {"_id": current_book["_id"]},
                {
                    "$set": {
                        "ai_review": review,
                        "review_generated_at": datetime.utcnow()
                    }
                }
            )
            
            # Send review
            review_text = (
                f"📖 **Review: {current_book['title']}**\n"
                f"🤖 *Generated from {len(current_book['notes'])} notes*\n\n"
                f"{review}\n\n"
                f"---\n"
                f"💡 *This review was generated by AI based on your personal notes*"
            )
            
            await processing_msg.edit_text(review_text, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error generating review: {e}")
            await processing_msg.edit_text(
                "❌ Sorry, I couldn't generate a review right now. Please try again later."
            )
    
    async def generate_ai_review(self, book_title: str, notes_text: str) -> str:
        """Generate AI review using OpenAI"""
        prompt = f"""
Based on the following personal reading notes for the book "{book_title}", write a thoughtful and comprehensive book review. 

Reading Notes:
{notes_text}

Please write a review that covers:
1. Overall impression and rating
2. Key themes and main ideas
3. Strengths and notable aspects
4. Any criticisms or weaknesses mentioned
5. Personal takeaways and recommendations

Write in a personal, engaging tone as if the reader took these notes themselves. Keep it concise but insightful (200-400 words).
        """
        
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system", 
                    "content": "You are a helpful assistant that creates book reviews based on personal reading notes."
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )
        
        return response.choices[0].message.content.strip()
    
    async def finish_book_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Mark current book as finished"""
        user_id = update.effective_user.id
        
        current_book = self.get_current_book(user_id)
        if not current_book:
            await update.message.reply_text(
                "📚 No active book set! Use `/newbook <title>` to start tracking a book."
            )
            return
        
        # Mark book as finished
        self.books_collection.update_one(
            {"_id": current_book["_id"]},
            {
                "$set": {
                    "status": "finished",
                    "finished_at": datetime.utcnow()
                }
            }
        )
        
        # Clear user's current book
        self.users_collection.update_one(
            {"user_id": user_id},
            {"$set": {"current_book_id": None}}
        )
        
        note_count = len(current_book["notes"])
        
        await update.message.reply_text(
            f"✅ **{current_book['title']}** marked as finished!\n\n"
            f"📊 **Final stats:**\n"
            f"• {note_count} notes saved\n\n"
            f"Great job! Use `/newbook <title>` to start your next book.",
            parse_mode='Markdown'
        )
    
    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show user reading statistics"""
        user_id = update.effective_user.id
        
        # Get all user books
        books = list(self.books_collection.find({"user_id": user_id}))
        
        if not books:
            await update.message.reply_text(
                "📊 No reading stats yet!\n\n"
                "Start tracking books with `/newbook <title>`"
            )
            return
        
        # Calculate stats
        total_books = len(books)
        finished_books = len([b for b in books if b["status"] == "finished"])
        reading_books = total_books - finished_books
        total_notes = sum(len(book["notes"]) for book in books)
        
        # Find most noted book
        most_noted_book = max(books, key=lambda b: len(b["notes"]))
        
        stats_text = (
            f"📊 **Your Reading Statistics**\n\n"
            f"📚 **Books:** {total_books} total\n"
            f"✅ Finished: {finished_books}\n"
            f"📖 Currently reading: {reading_books}\n\n"
            f"📝 **Notes:** {total_notes} total\n"
            f"💫 Most noted: **{most_noted_book['title']}** "
            f"({len(most_noted_book['notes'])} notes)\n\n"
            f"🎉 Keep up the great reading!"
        )
        
        await update.message.reply_text(stats_text, parse_mode='Markdown')
    
    def get_current_book(self, user_id: int):
        """Get user's current active book"""
        user = self.users_collection.find_one({"user_id": user_id})
        if not user or not user.get("current_book_id"):
            return None
        
        return self.books_collection.find_one({"_id": user["current_book_id"]})
    
    def run(self):
        """Start the bot"""
        print("🤖 Starting BookNotes Bot...")
        print("📚 Ready to help you track your reading!")
        self.application.run_polling()

# Main execution
if __name__ == "__main__":
    # Check for required environment variables
    required_vars = ['TELEGRAM_BOT_TOKEN', 'OPENAI_API_KEY', 'MONGODB_URI']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print(f"❌ Missing required environment variables: {', '.join(missing_vars)}")
        print("\nPlease set the following environment variables:")
        print("• TELEGRAM_BOT_TOKEN - Get from @BotFather on Telegram")
        print("• OPENAI_API_KEY - Get from OpenAI API dashboard")
        print("• MONGODB_URI - MongoDB connection string")
        exit(1)
    
    # Start bot
    bot = BookNotesBot()
    bot.run()