# pylint: disable=all
# type: ignore
import json
import sys
import cv2
import os
import traceback
sys.path.append("pdf_extraction_pipeline")
from pdf_producer import check_ptm_completion_queue, error_queue
from rabbitmq_connection import get_rabbitmq_connection, get_channel
# sonali: added
from utils import read_image_from_str, get_mongo_collection

connection = get_rabbitmq_connection()
channel = get_channel(connection)

table_bank_book_job_details=get_mongo_collection('table_bank_book_job_details')
table_bank_done=get_mongo_collection('table_bank_done')

import layoutparser as lp

tablebank_model = lp.Detectron2LayoutModel('lp://TableBank/faster_rcnn_R_50_FPN_3x/config',
                                 extra_config=["MODEL.ROI_HEADS.SCORE_THRESH_TEST", 0.8],
                                 label_map={0: "Table"})

def tableBank_layout(ch, method, properties, body):
    try:
        message = json.loads(body)
        print(message)
        job = message['job']
        total_pages = message['total_pages']
        image_path = message["image_path"]
        page_num = message["page_num"]
        bookname = message["bookname"]
        bookId = message["bookId"]
        existing_page = table_bank_book_job_details.find_one({"bookId": bookId, "pages.page_num": page_num})
        if existing_page:
            if total_pages == (page_num + 1):
                check_ptm_completion_queue('check_ptm_completion_queue', bookname, bookId)
            else:
                return
        # image = cv2.imread(image_path)
        # sonali : read image from base64 encoded string to remove dependency from image path
        image_str = message["image_str"]
        image = read_image_from_str(image_str)
        image = image[..., ::-1]
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
        
        # sonali: added
        job_details = table_bank_book_job_details.find_one({"bookId": bookId})
        extracted_pages = len(job_details['pages'])
        if total_pages == extracted_pages:
        # if total_pages == (page_num + 1):
            new_ptm_book_document = {
                "bookId": bookId,
                "bookname": bookname,
                "ptm": "Table_bank done"
            }
            table_bank_done.insert_one(new_ptm_book_document)
            check_ptm_completion_queue('check_ptm_completion_queue', bookname, bookId)

    except Exception as e:
        error = {'consumer':'tableBank_consumer',"consumer_message":message, "page_num":page_num, "error":str(e), "line_number":traceback.extract_tb(e.__traceback__)[-1].lineno} 
        print(print(error))
        error_queue('error_queue',bookname, bookId, error)

    finally:
        ch.basic_ack(delivery_tag=method.delivery_tag)



def consume_table_bank_queue():
    try:
        channel.basic_qos(prefetch_count=1, global_qos=False)
        # Declare the queue
        channel.queue_declare(queue='table_bank_queue')

        # Set up the callback function for handling messages from the queue
        channel.basic_consume(queue='table_bank_queue', on_message_callback=tableBank_layout)

        print(' [*] Waiting for messages on table_bank_queue. To exit, press CTRL+C')
        channel.start_consuming()

    except KeyboardInterrupt:
        pass
    finally:
        channel.close()
        connection.close()


if __name__ == "__main__":
    try:
        consume_table_bank_queue()
    except KeyboardInterrupt:
        pass
