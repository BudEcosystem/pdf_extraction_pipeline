# pylint: disable=all
# type: ignore
import json
import sys
sys.path.append("pdf_extraction_pipeline")
import cv2
import traceback
from utils import timeit
from pdf_producer import check_ptm_completion_queue, error_queue
from rabbitmq_connection import get_rabbitmq_connection, get_channel
import layoutparser as lp
# sonali: added
from utils import read_image_from_str, get_mongo_collection

connection = get_rabbitmq_connection()
channel = get_channel(connection)


error_collection = get_mongo_collection('error_collection')
publaynet_book_job_details = get_mongo_collection('publaynet_book_job_details')
publaynet_done = get_mongo_collection('publaynet_done')



publaynet_model = lp.Detectron2LayoutModel('lp://PubLayNet/mask_rcnn_X_101_32x8d_FPN_3x/config',
                                 extra_config=["MODEL.ROI_HEADS.SCORE_THRESH_TEST", 0.8],
                                 label_map= {0: "Text", 1: "Title", 2: "List", 3: "Table", 4: "Figure"})

@timeit
def publaynet_layout(ch, method, properties, body):
    try:
        message = json.loads(body)
        print(message)
        job = message['job']
        total_pages = message['total_pages']
        image_path = message["image_path"]
        page_num = message["page_num"]
        bookname = message["bookname"]
        bookId = message["bookId"]
        existing_page = publaynet_book_job_details.find_one({"bookId": bookId, "pages.page_num": page_num})
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
        publaynet_layouts = publaynet_model.detect(image)
        layout_blocks = []
        for item in publaynet_layouts:
            if item.type != "Table":
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
            "job": job,
            'image_path': image_path,
            'status': 'done',
            'result': layout_blocks
        }
        existing_book = publaynet_book_job_details.find_one({"bookId": bookId})
        if existing_book:
            publaynet_book_job_details.update_one(
                {"_id": existing_book["_id"]},
                {"$push": {"pages": book_page_data}}
            )
        else:
            new_book_document = {
                "bookId": bookId,
                "bookname": bookname,
                "pages": [book_page_data]
            }
            publaynet_book_job_details.insert_one(new_book_document)
        
        # sonali: we should get page count from publaynet_book_job_details pages
        # because if we have multiple publaynet consumers then 5th page can come
        # for extraction before 2nd page
        job_details = publaynet_book_job_details.find_one({"bookId": bookId})
        extracted_pages = len(job_details['pages'])
        if total_pages == extracted_pages:
        # if total_pages == (page_num + 1):
            new_ptm_book_document = {
                "bookId": bookId,
                "bookname": bookname,
                "ptm": "PubLaynet done"
            }
            publaynet_done.insert_one(new_ptm_book_document)
            print("hello world ")
            check_ptm_completion_queue('check_ptm_completion_queue', bookname, bookId)
            print("hello world ")

    except Exception as e:
        error = {'consumer':"publaynet","consumer_message":message,"page_num":page_num,"error":str(e), "line_number":traceback.extract_tb(e.__traceback__)[-1].lineno} 
        print(print(error))
        error_queue('error_queue',bookname, bookId, error)
    finally:
        ch.basic_ack(delivery_tag=method.delivery_tag)



def consume_publaynet_queue():
    try:
        channel.basic_qos(prefetch_count=1, global_qos=False)
        # Declare the queue
        channel.queue_declare(queue='publeynet_queue')

        # Set up the callback function for handling messages from the queue
        channel.basic_consume(queue='publeynet_queue', on_message_callback=publaynet_layout)

        print(' [*] Waiting for messages on publeynet_queue. To exit, press CTRL+C')
        channel.start_consuming()

    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    try:
        consume_publaynet_queue()     
    except KeyboardInterrupt:
        pass
