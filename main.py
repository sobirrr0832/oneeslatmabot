import os
import logging
import datetime
import time
from dateutil.relativedelta import relativedelta
import pytz
import asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telegram.error import TelegramError
import warnings
from telegram.warnings import PTBUserWarning

# Suppress PTBUserWarning to avoid cluttering logs
warnings.filterwarnings('ignore', category=PTBUserWarning)

# .env faylidan sozlamalarni yuklash
load_dotenv()

# Logging sozlamalari
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot tokeni va boshqa o'zgaruvchilar
TOKEN = os.getenv('TELEGRAM_TOKEN')
if TOKEN:
    TOKEN = TOKEN.strip()
if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN topilmadi. .env faylini tekshiring yoki muhit o'zgaruvchisini to'g'ri o'rnating")

PORT = int(os.getenv('PORT', 8080))
WEBHOOK_URL = os.getenv('https://web-production-915a3.up.railway.app/')
RAILWAY_PUBLIC_DOMAIN = os.getenv('web-production-915a3.up.railway.app')
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///eslatma_bot.db')
TIMEZONE = pytz.timezone('Asia/Tashkent')

# Webhook URL ni aniqlash
if not WEBHOOK_URL and RAILWAY_PUBLIC_DOMAIN:
    WEBHOOK_URL = f"https://{web-production-915a3.up.railway.app}/{7789849508:AAH_jKNHQVrbVzVCfq8WMmoxH8fS-lqzg3A}"
elif not WEBHOOK_URL:
    logger.warning("WEBHOOK_URL yoki RAILWAY_PUBLIC_DOMAIN o'rnatilmagan, polling rejimi ishlatiladi (faqat mahalliy test uchun).")

# Conversation states
MAIN_MENU, ADDING_REMINDER, SET_TITLE, SET_DATE, SET_TIME, REMINDERS_LIST, CONFIRM_DELETE = range(7)

# Ma'lumotlar bazasi bilan ishlash uchun SQLAlchemy
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True)
    username = Column(String, nullable=True)
    first_name = Column(String)
    reminders = relationship("Reminder", back_populates="user", cascade="all, delete-orphan")

class Reminder(Base):
    __tablename__ = 'reminders'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    title = Column(String)
    date = Column(DateTime)
    is_recurring = Column(Boolean, default=False)
    recurring_type = Column(String, nullable=True)
    is_notified = Column(Boolean, default=False)
    user = relationship("User", back_populates="reminders")

# Ma'lumotlar bazasini yaratish
engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# Tugmalar va menyu
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton('📝 Yangi eslatma qo\'shish', callback_data='add_reminder')],
        [InlineKeyboardButton('📋 Eslatmalarim', callback_data='list_reminders')],
        [InlineKeyboardButton('ℹ️ Bot haqida', callback_data='about')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_cancel_keyboard():
    keyboard = [[InlineKeyboardButton('🔙 Bekor qilish', callback_data='cancel')]]
    return InlineKeyboardMarkup(keyboard)

def get_yes_no_keyboard():
    keyboard = [
        [
            InlineKeyboardButton('✅ Ha', callback_data='yes'),
            InlineKeyboardButton('❌ Yo\'q', callback_data='no')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_recurring_keyboard():
    keyboard = [
        [InlineKeyboardButton('🔄 Har yili', callback_data='yearly')],
        [InlineKeyboardButton('🔄 Har oyda', callback_data='monthly')],
        [InlineKeyboardButton('🔄 Har hafta', callback_data='weekly')],
        [InlineKeyboardButton('1️⃣ Bir martalik', callback_data='once')],
        [InlineKeyboardButton('🔙 Bekor qilish', callback_data='cancel')]
    ]
    return InlineKeyboardMarkup(keyboard)

# Bot kommandalari
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    logger.info(f"Start command received from user: {user.id} ({user.first_name})")
    
    # Ma'lumotlar bazasida foydalanuvchini tekshirish yoki qo'shish
    session = Session()
    
    try:
        db_user = session.query(User).filter_by(telegram_id=user.id).first()
        
        if not db_user:
            db_user = User(
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name
            )
            session.add(db_user)
        else:
            # Foydalanuvchi ma'lumotlarini yangilash
            db_user.username = user.username
            db_user.first_name = user.first_name
        
        session.commit()
        
        # Foydalanuvchi holatini tozalash
        context.user_data.clear()
        
        await update.message.reply_text(
            f"Assalomu alaykum, {user.first_name}! Eslatma botiga xush kelibsiz!\n\n"
            "Bu bot muhim sanalarda sizga eslatmalar yuboradi. Masalan tug'ilgan kunlar, uchrashuvlar va boshqa tadbirlar haqida.",
            reply_markup=get_main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Start buyrug'ida xatolik: {e}")
        await update.message.reply_text(
            "Botni ishga tushirishda xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring."
        )
    finally:
        session.close()
    
    return MAIN_MENU

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    logger.info(f"Button callback received: {query.data}")
    
    if query.data == 'add_reminder':
        await query.edit_message_text(
            "Eslatma nomini kiriting:",
            reply_markup=get_cancel_keyboard()
        )
        return SET_TITLE
    
    elif query.data == 'list_reminders':
        return await list_reminders(update, context)
    
    elif query.data == 'about':
        await query.edit_message_text(
            "📆 *Eslatma Bot* 📆\n\n"
            "Muhim sanalarni eslatib turuvchi bot. Tug'ilgan kunlar, uchrashuvlar va boshqa tadbirlar haqida o'z vaqtida xabar olishingiz mumkin.\n\n"
            "Buyruqlar:\n"
            "/start - Botni ishga tushirish\n"
            "/help - Yordam olish",
            parse_mode='Markdown',
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU
    
    elif query.data == 'cancel':
        await query.edit_message_text(
            "Amal bekor qilindi. Bosh menyuga qaytdingiz.",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU
    
    elif query.data in ['yearly', 'monthly', 'weekly', 'once']:
        context.user_data['recurring_type'] = None if query.data == 'once' else query.data
        context.user_data['is_recurring'] = query.data != 'once'
        
        session = Session()
        
        try:
            user = session.query(User).filter_by(telegram_id=update.effective_user.id).first()
            
            reminder = Reminder(
                user_id=user.id,
                title=context.user_data.get('title'),
                date=context.user_data.get('date'),
                is_recurring=context.user_data.get('is_recurring', False),
                recurring_type=context.user_data.get('recurring_type')
            )
            
            session.add(reminder)
            session.commit()
            
            await query.edit_message_text(
                f"✅ Eslatma muvaffaqiyatli qo'shildi!\n\n"
                f"📝 Sarlavha: {reminder.title}\n"
                f"📅 Sana: {reminder.date.strftime('%d.%m.%Y')}\n"
                f"🕒 Vaqt: {reminder.date.strftime('%H:%M')}\n"
                f"🔄 Takrorlanish: {get_recurring_text(reminder.recurring_type)}",
                reply_markup=get_main_menu_keyboard()
            )
        
        except Exception as e:
            logger.error(f"Error adding reminder: {e}")
            await query.edit_message_text(
                "❌ Eslatma qo'shishda xatolik yuz berdi. Qaytadan urinib ko'ring.",
                reply_markup=get_main_menu_keyboard()
            )
        
        finally:
            session.close()
        
        return MAIN_MENU
    
    elif query.data.startswith('delete_'):
        reminder_id = int(query.data.split('_')[1])
        context.user_data['delete_reminder_id'] = reminder_id
        
        await query.edit_message_text(
            "Eslatmani o'chirishni tasdiqlaysizmi?",
            reply_markup=get_yes_no_keyboard()
        )
        return CONFIRM_DELETE
    
    elif query.data == 'yes' and context.user_data.get('delete_reminder_id'):
        reminder_id = context.user_data['delete_reminder_id']
        session = Session()
        
        try:
            reminder = session.query(Reminder).filter_by(id=reminder_id).first()
            if reminder:
                session.delete(reminder)
                session.commit()
                await query.edit_message_text(
                    "✅ Eslatma muvaffaqiyatli o'chirildi!",
                    reply_markup=get_main_menu_keyboard()
                )
            else:
                await query.edit_message_text(
                    "❌ Eslatma topilmadi.",
                    reply_markup=get_main_menu_keyboard()
                )
        except Exception as e:
            logger.error(f"Error deleting reminder: {e}")
            await query.edit_message_text(
                "❌ Eslatmani o'chirishda xatolik yuz berdi.",
                reply_markup=get_main_menu_keyboard()
            )
        finally:
            session.close()
        
        return MAIN_MENU
    
    elif query.data == 'no':
        await query.edit_message_text(
            "Eslatmani o'chirish bekor qilindi.",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU
    
    elif query.data == 'back_to_list':
        return await list_reminders(update, context)
    
    elif query.data == 'back_to_menu':
        await query.edit_message_text(
            "Bosh menyu:",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU
        
    return MAIN_MENU

async def set_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = update.message.text
    context.user_data['title'] = title
    logger.info(f"Title set: {title}")
    
    await update.message.reply_text(
        "Eslatma sanasini kiriting (KK.OO.YYYY formatida):\nMasalan: 15.05.2025",
        reply_markup=get_cancel_keyboard()
    )
    
    return SET_DATE

async def set_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    date_text = update.message.text
    
    try:
        date = datetime.datetime.strptime(date_text, "%d.%m.%Y")
        context.user_data['date'] = date
        logger.info(f"Date set: {date_text}")
        
        await update.message.reply_text(
            "Eslatma vaqtini kiriting (SS:MM formatida):\nMasalan: 14:30",
            reply_markup=get_cancel_keyboard()
        )
        
        return SET_TIME
    except ValueError:
        logger.warning(f"Invalid date format: {date_text}")
        await update.message.reply_text(
            "❌ Noto'g'ri format. Iltimos, sanani KK.OO.YYYY formatida kiriting (masalan, 15.05.2025):",
            reply_markup=get_cancel_keyboard()
        )
        return SET_DATE

async def set_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    time_text = update.message.text
    
    try:
        time = datetime.datetime.strptime(time_text, "%H:%M").time()
        date = context.user_data['date']
        
        full_date = datetime.datetime.combine(date.date(), time).replace(tzinfo=TIMEZONE)
        context.user_data['date'] = full_date
        logger.info(f"Time set: {time_text}, Full date: {full_date}")
        
        await update.message.reply_text(
            "Eslatma turini tanlang:",
            reply_markup=get_recurring_keyboard()
        )
        
        return ADDING_REMINDER
    except ValueError:
        logger.warning(f"Invalid time format: {time_text}")
        await update.message.reply_text(
            "❌ Noto'g'ri format. Iltimos, vaqtni SS:MM formatida kiriting (masalan, 14:30):",
            reply_markup=get_cancel_keyboard()
        )
        return SET_TIME

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = update.effective_user.id
    logger.info(f"Listing reminders for user: {user_id}")
    
    session = Session()
    
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            text = "❌ Foydalanuvchi ma'lumotlari topilmadi."
        else:
            reminders = session.query(Reminder).filter_by(user_id=user.id).all()
            
            if not reminders:
                text = "📝 Sizda hech qanday eslatma yo'q."
            else:
                text = "📋 *Sizning eslatmalaringiz:*\n\n"
                
                for i, reminder in enumerate(reminders, 1):
                    text += f"*{i}. {reminder.title}*\n"
                    text += f"📅 Sana: {reminder.date.strftime('%d.%m.%Y')}\n"
                    text += f"🕒 Vaqt: {reminder.date.strftime('%H:%M')}\n"
                    text += f"🔄 Takrorlanish: {get_recurring_text(reminder.recurring_type)}\n"
                    
                    keyboard = [[InlineKeyboardButton(f"❌ O'chirish", callback_data=f"delete_{reminder.id}")]]
                    
                    if i < len(reminders):
                        text += "\n" + "-" * 20 + "\n\n"
    
        keyboard = [[InlineKeyboardButton("🔙 Orqaga", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.edit_message_text(
                text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error listing reminders: {e}")
        text = "❌ Eslatmalarni ko'rishda xatolik yuz berdi."
        
        if query:
            await query.edit_message_text(text, reply_markup=get_main_menu_keyboard())
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=get_main_menu_keyboard()
            )
    
    finally:
        session.close()
    
    return REMINDERS_LIST

def get_recurring_text(recurring_type):
    if recurring_type == 'yearly':
        return "Har yili"
    elif recurring_type == 'monthly':
        return "Har oyda"
    elif recurring_type == 'weekly':
        return "Har hafta"
    else:
        return "Bir martalik"

# APScheduler orqali eslatmalarni tekshirish
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

async def check_reminders_task(application):
    now = datetime.datetime.now(TIMEZONE)
    logger.info(f"Checking reminders at {now}")
    session = Session()
    
    try:
        reminders = session.query(Reminder).filter(
            Reminder.date <= now,
            Reminder.is_notified == False
        ).all()
        
        logger.info(f"Found {len(reminders)} reminders to notify")
        
        for reminder in reminders:
            user = session.query(User).filter_by(id=reminder.user_id).first()
            
            if user:
                try:
                    await application.bot.send_message(
                        chat_id=user.telegram_id,
                        text=f"⏰ *ESLATMA!*\n\n"
                             f"📝 *{reminder.title}*\n"
                             f"📅 Sana: {reminder.date.strftime('%d.%m.%Y')}\n"
                             f"🕒 Vaqt: {reminder.date.strftime('%H:%M')}",
                        parse_mode='Markdown'
                    )
                    
                    reminder.is_notified = True
                    
                    if reminder.is_recurring:
                        next_date = None
                        
                        if reminder.recurring_type == 'yearly':
                            next_date = reminder.date + relativedelta(years=1)
                        elif reminder.recurring_type == 'monthly':
                            next_date = reminder.date + relativedelta(months=1)
                        elif reminder.recurring_type == 'weekly':
                            next_date = reminder.date + relativedelta(weeks=1)
                        
                        if next_date:
                            new_reminder = Reminder(
                                user_id=reminder.user_id,
                                title=reminder.title,
                                date=next_date,
                                is_recurring=reminder.is_recurring,
                                recurring_type=reminder.recurring_type,
                                is_notified=False
                            )
                            session.add(new_reminder)
                            logger.info(f"Created new recurring reminder for {next_date}")
                    
                except Exception as e:
                    logger.error(f"Eslatma yuborishda xatolik: {e}")
                    continue
                    
                session.commit()
    
    except Exception as e:
        logger.error(f"Eslatmalarni tekshirishda xatolik: {e}")
    finally:
        session.close()

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.info("Help command received")
    await update.message.reply_text(
        "📌 *Eslatma Bot yordam*\n\n"
        "Bu bot orqali siz muhim sanalarda eslatmalar olishingiz mumkin.\n\n"
        "*Asosiy buyruqlar:*\n"
        "/start - Botni ishga tushirish\n"
        "/help - Yordam ko'rsatish\n\n"
        "*Bot imkoniyatlari:*\n"
        "- Muhim sanalar uchun eslatmalar qo'shish\n"
        "- Bir martalik yoki takrorlanuvchi eslatmalar yaratish\n"
        "- Eslatmalarni boshqarish va o'chirish",
        parse_mode='Markdown',
        reply_markup=get_main_menu_keyboard()
    )
    return MAIN_MENU

async def set_webhook(application):
    """Telegram webhook-ni o'rnatish"""
    try:
        await application.bot.deleteWebhook()
        await application.bot.setWebhook(url=WEBHOOK_URL)
        logger.info(f"Webhook muvaffaqiyatli o'rnatildi: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Webhook o'rnatishda xatolik: {e}")
        raise e

def main():
    if not TOKEN:
        logger.error("TELEGRAM_TOKEN o'rnatilmagan! .env faylini tekshiring yoki muhit o'zgaruvchisini to'g'ri o'rnating")
        return
        
    application = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(button_handler),
                CommandHandler('start', start)
            ],
            SET_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_title),
                CallbackQueryHandler(button_handler)
            ],
            SET_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_date),
                CallbackQueryHandler(button_handler)
            ],
            SET_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_time),
                CallbackQueryHandler(button_handler)
            ],
            ADDING_REMINDER: [
                CallbackQueryHandler(button_handler)
            ],
            REMINDERS_LIST: [
                CallbackQueryHandler(button_handler)
            ],
            CONFIRM_DELETE: [
                CallbackQueryHandler(button_handler)
            ]
        },
        fallbacks=[
            CommandHandler('start', start),
            CommandHandler('help', help_command)
        ]
    )
    
    application.add_handler(conv_handler)
    
    scheduler = AsyncIOScheduler(job_defaults={'misfire_grace_time': 300})
    scheduler.add_job(
        check_reminders_task,
        IntervalTrigger(minutes=1),
        id='check_reminders',
        args=[application]
    )
    scheduler.start()
    
    logger.info("Bot ishga tushirilmoqda...")
    try:
        if WEBHOOK_URL:
            logger.info(f"Webhook rejimida ishga tushirilmoqda: {WEBHOOK_URL}")
            application.run_webhook(
                listen="0.0.0.0",
                port=PORT,
                url_path=TOKEN,
                webhook_url=WEBHOOK_URL
            )
            asyncio.get_event_loop().run_until_complete(set_webhook(application))
        else:
            logger.info("Polling rejimida ishga tushirilmoqda (mahalliy test uchun)")
            while True:
                try:
                    application.run_polling(allowed_updates=Update.ALL_TYPES)
                    break
                except TelegramError as e:
                    if "Conflict" in str(e):
                        logger.warning("Conflict detected, retrying in 5 seconds...")
                        time.sleep(5)
                    else:
                        raise e
    except Exception as e:
        logger.error(f"Botni ishga tushirishda xatolik: {e}")

if __name__ == '__main__':
    main()
