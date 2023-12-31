# pylint: disable=all
# type: ignore
from dotenv import load_dotenv
import sys
import traceback
import shutil
sys.path.append("pdf_extraction_pipeline")
from utils import timeit
load_dotenv()
import os
import json
from datetime import datetime
from pdf_producer import error_queue
from rabbitmq_connection import get_rabbitmq_connection, get_channel
from utils import get_mongo_collection

connection = get_rabbitmq_connection()
channel = get_channel(connection)

bookdata = get_mongo_collection('book_set_2_new')
book_details = get_mongo_collection('book_details')
nougat_pages_db=get_mongo_collection('nougat_pages')
book_other_pages=get_mongo_collection('book_other_pages')
nougat_done=get_mongo_collection('nougat_done')
book_other_pages_done=get_mongo_collection('book_other_pages_done')
latex_pages=get_mongo_collection('latex_pages')
latex_pages_done=get_mongo_collection('latex_pages_done')

folder_name=os.environ['BOOK_FOLDER_NAME']

@timeit
def book_complete(ch, method, properties, body):
    try:
        message = json.loads(body)
        bookname = message["bookname"]
        bookId = message["bookId"]
        print(bookId)
        book_already_completed=bookdata.find_one({"bookId":bookId})
        if book_already_completed:
            print("book already extracted")
            return
        other_pages = book_other_pages_done.find_one({"bookId": bookId})
        nougat_pages_done = nougat_done.find_one({"bookId": bookId})
        latex_ocr_pages = latex_pages_done.find_one({"bookId": bookId})
        
        # Check if all three documents are present
        if other_pages and nougat_pages_done and latex_ocr_pages:
            book_pages_document = book_other_pages.find_one({"bookId": bookId})
            nougat_pages_document = nougat_pages_db.find_one({"bookId": bookId})
            latex_pages_document = latex_pages.find_one({"bookId": bookId})

            # Initialize lists to hold pages from each document
            book_pages_result = book_pages_document.get("pages", []) if book_pages_document else []
            nougat_pages_result = nougat_pages_document.get("pages", []) if nougat_pages_document else []
            latex_pages_result = latex_pages_document.get("pages", []) if latex_pages_document else []

            # Count the number of present documents
            present_documents_count = sum(
                bool(doc) for doc in [book_pages_document, nougat_pages_document, latex_pages_document]
            )

            if present_documents_count >= 2:
                # If two or more documents are present, sort the pages
                all_pages =  book_pages_result + nougat_pages_result +  latex_pages_result
                sorted_pages = sorted(all_pages, key=lambda x: int(x.get("page_num", 0)))
                new_document = {
                    "bookId": bookId,
                    "book": bookname,
                    "pages": sorted_pages,
                }
            else:
                # If only one document is present, do not sort
                pages_to_add = book_pages_result + nougat_pages_result +  latex_pages_result
                new_document = {
                    "bookId": bookId,
                    "book": bookname,
                    "pages": pages_to_add,
                }
            bookdata.insert_one(new_document)     
            current_time = datetime.now().strftime("%H:%M:%S")
            book_details.update_one(
                {"bookId": bookId},
                {"$set": {"status": "extracted", "end_time": current_time}},
            )
            book_folder=bookname.split(".")[0]
            book_folder_path = os.path.abspath(book_folder)
            if os.path.exists(book_folder_path):
                shutil.rmtree(book_folder)
            book_path = os.path.join(folder_name, bookname)
            book_path = os.path.abspath(book_path)
            if os.path.exists(book_path):
                os.remove(book_path)      
        else:
            print("Not yet completed")
    except Exception as e:
        error = {"consumer":"book_completion_consumer","consumer_message":message,"error": str(e), "line_number": traceback.extract_tb(e.__traceback__)[-1].lineno}
        print(error)
        error_queue('error_queue', bookname, bookId, error)
    finally:
        ch.basic_ack(delivery_tag=method.delivery_tag)


def consume_book_completion_queue():
    try:
        channel.basic_qos(prefetch_count=1, global_qos=False)

        channel.queue_declare(queue='book_completion_queue')

        # Set up the callback function for handling messages from the queue
        channel.basic_consume(queue='book_completion_queue', on_message_callback=book_complete)

        print(' [*] Waiting for messages on book_completion_queue. To exit, press CTRL+C')
        channel.start_consuming()

    except KeyboardInterrupt:
        pass
    finally:
        channel.close()
        connection.close()

if __name__ == "__main__":
    try:
        consume_book_completion_queue()      
    except KeyboardInterrupt:
        pass