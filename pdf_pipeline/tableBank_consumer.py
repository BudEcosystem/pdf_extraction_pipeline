import pika
import json
import sys
import cv2
import os
sys.path.append("pdf_extraction_pipeline")
from utils import timeit
from PIL import Image
import pymongo
from pdf_producer import check_ptm_completion_queue
from model_loader import ModelLoader 
from rabbitmq_connection import get_rabbitmq_connection, get_channel

connection = get_rabbitmq_connection()
channel = get_channel(connection)

aws_access_key_id = os.environ['AWS_ACCESS_KEY_ID']
aws_secret_access_key = os.environ['AWS_SECRET_ACCESS_KEY']
aws_region = os.environ['AWS_REGION']

client = pymongo.MongoClient(os.environ['DATABASE_URL'])
db = client.bookssssss
table_bank_book_job_details=db.table_bank_book_job_details
table_bank_done=db.table_bank_done

def tableBank_layout(ch, method, properties, body):
    try:
        print("hello")
        message = json.loads(body)
        print(message)
        job = message['job']
        total_pages = message['total_pages']
        image_path = message["image_path"]
        page_num = message["page_num"]
        bookname = message["bookname"]
        bookId = message["bookId"]

        image = cv2.imread(image_path)
        image = image[..., ::-1] 
        tablebank = ModelLoader("TableBank")
        tablebank_model = tablebank.model
        tablebank_layouts = tablebank_model.detect(image)
        layout_blocks = []
        for item in tablebank_layouts:  
            output_item = {
                "x_1": item.block.x_1,
                "y_1": item.block.y_1,
                "x_2": item.block.x_2,
                "y_2": item.block.y_2,
                'type': item.type
            }
            layout_blocks.append(output_item)
        book_page_data = {
            'page_num': page_num,
            'image_path': image_path,
            "job": job,
            'status': 'done',
            'result': layout_blocks
        }
        existing_book = table_bank_book_job_details.find_one({"bookId": bookId})
        if existing_book:
            table_bank_book_job_details.update_one(
                {"_id": existing_book["_id"]},
                {"$push": {"pages": book_page_data}}
            )
        else:
            new_book_document = {
                "bookId": bookId,
                "bookname": bookname,
                "pages": [book_page_data]
            }
            table_bank_book_job_details.insert_one(new_book_document)

        if total_pages == (page_num + 1):
            new_ptm_book_document = {
                "bookId": bookId,
                "bookname": bookname,
                "ptm": "Table_bank done"
            }
            table_bank_done.insert_one(new_ptm_book_document)
            check_ptm_completion_queue('check_ptm_completion_queue', bookname, bookId)

    except Exception as e:
        print(f"An error occurred: {str(e)}")

    finally:
        ch.basic_ack(delivery_tag=method.delivery_tag)



def consume_table_bank_queue():
    try:
        # Declare the queue
        channel.queue_declare(queue='table_bank_queue')

        # Set up the callback function for handling messages from the queue
        channel.basic_consume(queue='table_bank_queue', on_message_callback=tableBank_layout)

        print(' [*] Waiting for messages on table_bank_queue. To exit, press CTRL+C')
        channel.start_consuming()

    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    try:
        consume_table_bank_queue()
    except KeyboardInterrupt:
        pass
