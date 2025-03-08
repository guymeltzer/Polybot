import telebot
from loguru import logger
import os
import time
import requests
from telebot.types import InputFile
import boto3


class ObjectDetectionBot:
    def __init__(self, token, telegram_app_url, s3_bucket_name):
        self.telegram_bot_client = telebot.TeleBot(token)
        self.s3_bucket_name = s3_bucket_name
        self.s3_client = boto3.client('s3',
                                      aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
                                      aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
                                      region_name='eu-north-1')

        if not telegram_app_url:
            raise ValueError("TELEGRAM_APP_URL is missing")

        # Remove old webhooks and set the new one
        try:
            self.telegram_bot_client.remove_webhook()
            time.sleep(0.5)
            webhook_url = f'{telegram_app_url}/{token}/'
            self.telegram_bot_client.set_webhook(url=webhook_url, timeout=60)
            logger.info(f'Webhook set: {webhook_url}')
        except Exception as e:
            logger.error(f"Failed to set webhook: {e}")

    def send_text(self, chat_id, text):
        try:
            self.telegram_bot_client.send_message(chat_id, text)
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Failed to send message to chat {chat_id}. Error: {e}")
        except Exception as e:
            logger.error(f"Unknown error occurred while sending message to chat {chat_id}. Error: {e}")

    def is_current_msg_photo(self, msg):
        return msg.photo is not None

    def download_user_photo(self, msg):
        if not msg.photo:
            raise RuntimeError('Message does not contain a photo')

        file_id = msg.photo[-1].file_id
        file_info = self.telegram_bot_client.get_file(file_id)
        data = self.telegram_bot_client.download_file(file_info.file_path)

        folder_name = 'photos'
        os.makedirs(folder_name, exist_ok=True)

        file_path = os.path.join(folder_name, os.path.basename(file_info.file_path))
        with open(file_path, 'wb') as photo:
            photo.write(data)

        return file_path

    def send_photo(self, chat_id, img_path):
        if not os.path.exists(img_path):
            logger.error(f"Image path does not exist: {img_path}")
            return

        with open(img_path, 'rb') as img:
            self.telegram_bot_client.send_photo(chat_id, img)

    def upload_to_s3(self, file_path):
        try:
            s3_key = os.path.basename(file_path)
            self.s3_client.upload_file(file_path, self.s3_bucket_name, s3_key)
            s3_url = f'https://{self.s3_bucket_name}.s3.amazonaws.com/{s3_key}'
            logger.info(f"Uploaded to S3: {s3_url}")
            return s3_url
        except Exception as e:
            logger.error(f"Failed to upload {file_path} to S3. Error: {e}")
            return None

    def get_yolo5_results(self, img_name):
        try:
            logger.info(f'Sending imgName to YOLOv5: {img_name}')
            response = requests.post("http://yolov5-service:8081/predict", json={"imgName": img_name})
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"YOLOv5 service error: {e}")
            return None

    def handle_message(self, msg):
        try:
            hardcoded_chat_id = msg.chat.id  # Use actual chat ID instead of hardcoded one
            logger.info(f"Handling message from chat ID: {hardcoded_chat_id}")

            if msg.photo:
                try:
                    logger.info('Downloading user photo...')
                    photo_path = self.download_user_photo(msg)
                    logger.info(f'Photo saved at {photo_path}')

                    logger.info('Uploading to S3...')
                    image_url = self.upload_to_s3(photo_path)
                    if not image_url:
                        self.send_text(hardcoded_chat_id, "Failed to upload image to S3.")
                        return

                    self.send_text(hardcoded_chat_id, f"Image uploaded: {image_url}")

                    logger.info('Sending to YOLOv5...')
                    img_name = os.path.basename(photo_path)
                    yolo_results = self.get_yolo5_results(img_name)

                    if yolo_results and 'predictions' in yolo_results:
                        detected_objects = [obj['class'] for obj in yolo_results['predictions']]
                        results_text = f"Detected: {', '.join(detected_objects)}" if detected_objects else "No objects detected."
                    elif yolo_results is None:
                        results_text = "Error processing the image."
                    else:
                        results_text = "No predictions found."

                    self.send_text(hardcoded_chat_id, results_text)

                except Exception as e:
                    logger.error(f"Processing error: {e}")
                    error_message = f"Error processing the image: {str(e)}"
                    self.send_text(hardcoded_chat_id, error_message)
            else:
                self.send_text(hardcoded_chat_id, "Please send a photo.")

        except Exception as e:
            logger.error(f"Error handling message: {e}")
