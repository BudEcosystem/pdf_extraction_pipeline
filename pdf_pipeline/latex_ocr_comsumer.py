# pylint: disable=all
# type: ignore
from dotenv import load_dotenv
import pytesseract
import traceback
import sys
sys.path.append("pdf_extraction_pipeline/code")
sys.path.append("pdf_extraction_pipeline")
from PIL import Image
import os
import boto3
import re
import uuid
import cv2
from latext import latex_to_text
from pix2tex.cli import LatexOCR
from utils import (
    timeit,
    crop_image,
    create_image_from_str,
    generate_image_str,
    get_mongo_collection
)
import json
from pdf_producer import book_completion_queue, error_queue, table_queue
from rabbitmq_connection import get_rabbitmq_connection, get_channel


connection = get_rabbitmq_connection()
channel = get_channel(connection)

load_dotenv()

# Configure AWS credentials
aws_access_key_id = os.environ['AWS_ACCESS_KEY_ID']
aws_secret_access_key = os.environ['AWS_SECRET_ACCESS_KEY']
aws_region = os.environ['AWS_REGION']

# Create an S3 client
s3 = boto3.client('s3',
                   aws_access_key_id=aws_access_key_id,
                   aws_secret_access_key=aws_secret_access_key,
                   region_name=aws_region)

bucket_name = os.environ['AWS_BUCKET_NAME']
folder_name=os.environ['BOOK_FOLDER_NAME']

model = LatexOCR()

latex_pages = get_mongo_collection('latex_pages')
latex_pages_done = get_mongo_collection('latex_pages_done')


def extract_latex_pages(ch, method, properties, body):
    try:
        message = json.loads(body)
        total_latex_pages=message['total_latex_pages']
        page_data=message['page_result']
        bookname = message["bookname"]
        bookId = message["bookId"]
        print(f"latex received {bookname} : {bookId} : {total_latex_pages}")
        latex_pages_doc=latex_pages_done.find_one({"bookId":bookId})
        if latex_pages_doc:
            print("latex pages already extracted")
            return 
        page_obj= process_pages(page_data, bookname, bookId)
        document=latex_pages.find_one({'bookId':bookId})
        if document:
            latex_pages.update_one({"_id":document["_id"]}, {"$push": {"pages": page_obj}})
        else:
            new_book_document = {
                "bookId": bookId,
                "book": bookname,  
                "pages": [page_obj]
            }
            latex_pages.insert_one(new_book_document)
        latex_page_extraction = latex_pages.find_one({"bookId": bookId})
        extracted_pages = len(latex_page_extraction["pages"])
        if total_latex_pages == extracted_pages:
            latex_pages_done.insert_one({
                "bookId": bookId,
                "book": bookname,
                "status": "latex pages Done"})
            book_completion_queue("book_completion_queue", bookname, bookId)
    except Exception as e:
        error = {"consumer":"latex_ocr_consumer","consumer_message":message,"page_num":page_num, "error":str(e), "line_number":traceback.extract_tb(e.__traceback__)[-1].lineno} 
        print(print(error))
        error_queue('error_queue',bookname, bookId, error)
    finally:
        print("ack received")
        ch.basic_ack(delivery_tag=method.delivery_tag)


def process_pages(page, bookname, bookId):
    page_obj = {}
    try:
        page_tables = []
        page_figures = []
        page_equations = []
        results = page.get("results", [])
        image_str = page['image_str']
        new_image_path = create_image_from_str(image_str)
        page['image_path'] = new_image_path
        pdFigCap = page.get("pdFigCap", False)
        page_num = page.get("page_num", "")
        page_content = sort_text_blocks_and_extract_data(results, new_image_path, page_figures, page_equations, pdFigCap, bookname,bookId,page_num)
        page_obj={
            "page_num": page_num,
            "text": page_content,
            "tables": page_tables,
            "figures": page_figures,
            "equations": page_equations
        }
        os.remove(new_image_path)
    except Exception as e:
        print("error while page in latex process_pages",e)
    return page_obj

def sort_text_blocks_and_extract_data(blocks, imagepath, page_figures, page_equations, pdFigCap, bookname, bookId, page_num):
    try:
        print("hello")
        sorted_blocks = sorted(blocks, key=lambda block: (block['y_1'] + block['y_2']) / 2)
        # print(sorted_blocks)
        output = ""
        prev_block = None
        next_block = None
        for i, block in enumerate(sorted_blocks): 
            if i > 0:
                prev_block = sorted_blocks[i - 1]
            if i < len(sorted_blocks) - 1:
                next_block = sorted_blocks[i + 1]  
            if block['type'] == "Table":
                output = process_table(block,imagepath, output, bookname,bookId, page_num)
            elif block['type'] == "Figure":
                if pdFigCap:
                    output = process_figure(block, imagepath, output, page_figures)
                else:
                    output=process_publeynet_figure(block, imagepath, prev_block, next_block, output, page_figures)  
            elif block['type'] == "Text":
                output = process_text(block, imagepath, output)
            elif block['type'] == "Title":
                output = process_title(block, imagepath, output)
            elif block['type'] == "List":
                output = process_list(block, imagepath, output)
            elif block['type']=='Equation':
                output=process_equation(block, imagepath, output, page_equations)

        page_content = re.sub(r'\s+', ' ', output).strip()
        return page_content
    except Exception as e:
        print('error while sorting,',e)


@timeit
def process_table(table_block,imagepath, output, bookname, bookId, page_num):
    x1, y1, x2, y2 = table_block['x_1'], table_block['y_1'], table_block['x_2'], table_block['y_2']
    img = cv2.imread(imagepath)
    y1 -= 70
    if y1 < 0:
        y1 = 0
    x1 = 0
    x2 += 20
    if x2 > img.shape[1]:
        x2 = img.shape[1]
    y2 += 20
    if y2 > img.shape[0]:
        y2 = img.shape[0]
    cropped_image = img[int(y1):int(y2), int(x1):int(x2)] 
    tableId = uuid.uuid4().hex 
    table_image_path =os.path.abspath(f"cropeed{tableId}.png")
    cv2.imwrite(table_image_path, cropped_image)
    output += f"{{{{table:{tableId}}}}}"
    data = {'img': generate_image_str(table_image_path)}
    table_queue('table_queue',tableId,data,page_num,bookname,bookId)
    if os.path.exists(table_image_path):
        os.remove(table_image_path)
    return output


@timeit
def process_figure(figure_block, imagepath, output, page_figures):
    try:
        figureId = uuid.uuid4().hex
        figure_image_path = crop_image(figure_block,imagepath, figureId)
        output += f"{{{{figure:{figureId}}}}}"

        figure_url=upload_to_aws_s3(figure_image_path, figureId)
        page_figures.append({
            "id":figureId,
            "url":figure_url,
            "caption": figure_block['caption']
        })
        if os.path.exists(figure_image_path):
            os.remove(figure_image_path)
        return output  
    except Exception as e:
        print("error while figure",e)  

@timeit
def process_publeynet_figure(figure_block, imagepath, prev_block, next_block, output, page_figures):
    print("publeynbeje")
    caption=""
    figureId = uuid.uuid4().hex
    figure_image_path =crop_image(figure_block,imagepath, figureId)
    # print(figure_image_path)
    output += f"{{{{figure:{figureId}}}}}"

    if prev_block:
        prevId=uuid.uuid4().hex
        prev_image_path = crop_image(prev_block,imagepath, prevId)
        #extraction of text from cropped image using pytesseract
        image =Image.open(prev_image_path)
        text = pytesseract.image_to_string(image)
        text = re.sub(r'\s+', ' ', text).strip()
        pattern = r"(Fig\.|Figure)\s+\d+"
        match = re.search(pattern, text)
        if match:
            caption = text
        if os.path.exists(prev_image_path):
            os.remove(prev_image_path)

    if next_block:
        nextId=uuid.uuid4().hex
        next_image_path = crop_image(next_block,imagepath, nextId) 
        #extraction of text from cropped image using pytesseract
        image =Image.open(next_image_path)
        text = pytesseract.image_to_string(image)
        text = re.sub(r'\s+', ' ',text).strip()
        pattern = r"(Fig\.|Figure)\s+\d+"
        match = re.search(pattern, text)
        if match:
            caption = text
        if os.path.exists(next_image_path):
            os.remove(next_image_path)

    figure_url=upload_to_aws_s3(figure_image_path, figureId)
    page_figures.append({
        "id":figureId,
        "url":figure_url,
        "caption":caption
    })
    if os.path.exists(figure_image_path):
        os.remove(figure_image_path)
    return output    

@timeit
def process_text(text_block,imagepath, output):
    try:
        textId=uuid.uuid4().hex
        cropped_image_path = crop_image(text_block,imagepath, textId)
        image =Image.open(cropped_image_path)
        text = pytesseract.image_to_string(image)
        output+=text
        if os.path.exists(cropped_image_path):
            os.remove(cropped_image_path)
        return output
    except Exception as e:
        print("error while process text",e)  
    
    
@timeit
def process_title(title_block,imagepath, output):
    try:
        titleId=uuid.uuid4().hex
        cropped_image_path = crop_image(title_block,imagepath, titleId)
        #extraction of text from cropped image using pytesseract
        image =Image.open(cropped_image_path)
        text = pytesseract.image_to_string(image)
        output+=text
        if os.path.exists(cropped_image_path):
            os.remove(cropped_image_path)
        return output
    except Exception as e:
        print("error while process title",e)  
    

@timeit
def process_list(list_block,imagepath, output):
    try:
        listId=uuid.uuid4().hex
        cropped_image_path = crop_image(list_block,imagepath, listId)
        image =Image.open(cropped_image_path)
        text = pytesseract.image_to_string(image)
        output+=text
        if os.path.exists(cropped_image_path):
            os.remove(cropped_image_path)
        return output
    except Exception as e:
        print("error while process list",e)  
    
@timeit
def process_equation(equation_block, imagepath, output, page_equations):
    try:
        print("hello")
        equationId=uuid.uuid4().hex
        equation_image_path = crop_image(equation_block,imagepath, equationId)
        # print(equation_image_path)
        output += f"{{{{equation:{equationId}}}}}"
        img = Image.open(equation_image_path)
        # print(img)
        latex_text= model(img)
        text_to_speech=latext_to_text_to_speech(latex_text)
        page_equations.append(
            {'id': equationId, 'text':latex_text, 'text_to_speech':text_to_speech}
            )
        if os.path.exists(equation_image_path):
            os.remove(equation_image_path)
        return output
    except Exception as e:
        print("error while equation",e)  
 
@timeit
def upload_to_aws_s3(figure_image_path, figureId): 
    folderName=os.environ['AWS_PDF_IMAGE_UPLOAD_FOLDER']
    s3_key = f"{folderName}/{figureId}.png"
    # Upload the image to the specified S3 bucket
    s3.upload_file(figure_image_path, bucket_name, s3_key)
    # Get the URL of the uploaded image
    figure_url = f"https://{bucket_name}.s3.amazonaws.com/{s3_key}"

    return figure_url

@timeit
def latext_to_text_to_speech(text):
    try:
        text = "${}$".format(text.lstrip('\\'))
        text_to_speech = latex_to_text(text)
        return text_to_speech
    except Exception as e:
        print('error while text to speech',e)


def consume_latex_ocr_queue():
    try:
        channel.basic_qos(prefetch_count=1, global_qos=False)

        # Declare the queue
        channel.queue_declare(queue='latex_ocr_queue')

        # Set up the callback function for handling messages from the queue
        channel.basic_consume(queue='latex_ocr_queue', on_message_callback=extract_latex_pages)

        print(' [*] Waiting for messages on latec_ocr_queue To exit, press CTRL+C')
        channel.start_consuming()

    except KeyboardInterrupt:
        pass
    finally:
        connection.close()

   


if __name__ == "__main__":
    consume_latex_ocr_queue()