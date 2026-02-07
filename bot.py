import os
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

BOT_TOKEN= os.getenv("BOT_TOKEN")
 
SOURCE_CHAT_ID = -5146530739    # baad me bharenge
DEST_CHAT_ID = -5140519863    # baad me bharenge

async def forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id == SOURCE_CHAT_ID:
        await context.bot.forward_message(
            chat_id=DEST_CHAT_ID,
            from_chat_id=SOURCE_CHAT_ID,
            message_id=update.message.message_id
        )

app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(MessageHandler(filters.ALL, forward))
app.run_polling()
