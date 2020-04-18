from urllib.request import urlopen
import re
import requests
from bs4 import BeautifulSoup
import bs4
import pandas as pd
import numpy as np
import textwrap
from prettytable import PrettyTable


def get_cik(comps):
    cik = {}
    for comp in comps:
        comp_ = comp.split('.')[0].replace(' ','+')
        link = ("https://www.edgarcompany.sec.gov/servlet/CompanyDBSearch?start_row=-1&end_row=-1&main_back=0&cik=&"
                f"company_name={comp_}&reporting_file_number=&series_id=&series_name=&class_contract_id="
                "&class_contract_name=&state_country=NONE&city=&state_incorporation=NONE&zip_code=&last_update_from=&"
                "last_update_to=&page=summary&submit_button=Submit")
        data = requests.get(link).text
        regex = re.compile(r"cik=\d{10}")
        for i in regex.finditer(data):
            cik[comp] = (i.group()[4:])
    return cik

def get_links(cik, type_, priorto, count):
    link = f"http://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik} \
            &type={type_}&dateb={priorto}&owner=exclude&output=xml&count={count}"
    
    # parse the website and extract links
    data = requests.get(link).text
    soup = BeautifulSoup(data, "lxml")
    
    comp = soup.find_all('name')[-1].string #company name
    dates = soup.find_all('datefiled')     #dates of files
    types = soup.find_all('type')          #types of files
    links = soup.find_all('filinghref')    #links of files
    
    files = {} #store the file-link pair in files dict
    for date,Type,link in zip(dates,types,links):
        file = '|'.join([comp,Type.string,date.string])
        # convert http://*-index.htm to http://*.txt
        url = link.string
        if link.string.split(".")[len(link.string.split("."))-1] == "htm":
            url += "l"
        required_url = url.replace('-index.html', '.txt')
        
        #update files dict
        files[file] = required_url
        
    return files

#clean html
def clean_soup(html):
    data = requests.get(html).text
    soup = BeautifulSoup(data, "lxml")
    blacklist = ["script", "style"]
    attrlist = ["class", "id", "name", "style", 'cellpadding', 'cellspacing']
    skiptags = ['font', 'a', 'b', 'i', 'u']
    
    for tag in soup.findAll():
        if tag.name.lower() in blacklist:
            # blacklisted tags are removed in their entirety
            tag.extract()

        if tag.name.lower() in skiptags:
            tag.replaceWithChildren()
            
        for attribute in attrlist:
            del tag[attribute]
            
    return soup

#from S-1 extract sections starting from key_1 and ending before key_2
def extract_section(doc,url,key_1,key_2):
    raw_file = requests.get(url).text.replace('\n',' ')
    type_ = doc.split('|')[1]
    # Write regexes
    doc_start_pattern = re.compile(r'<DOCUMENT>')
    doc_end_pattern = re.compile(r'</DOCUMENT>')
    type_pattern = re.compile(f'<TYPE>{type_}')

    # Create 3 lists with the span idices for each regex
    doc_start_is = [x.end() for x in doc_start_pattern.finditer(raw_file)]
    doc_end_is = [x.start() for x in doc_end_pattern.finditer(raw_file)]
    doc_types = [x[len('<TYPE>'):] for x in type_pattern.findall(raw_file)]
        
    document = {}
    # Create a loop to go through each section type and save only the S-1 section in the dictionary
    for doc_type, doc_start_i, doc_end_i in zip(doc_types, doc_start_is, doc_end_is):
        if doc_type == type_:
            document[doc_type] = raw_file[doc_start_i:doc_end_i]
    
    regex = re.compile(f'{key_1}|{key_2}')
    matches = regex.finditer(document[type_])
    sections = [(x.group(),x.start(),x.end()) for x in matches]
    section_title = [section[0] for section in sections]
    
    if key_1 not in section_title or key_2 not in section_title:
        #print (f"key1/key2 not properly found in {doc}")
        return []
    if key_1 == 'DESCRIPTION OF OTHER INDEBTEDNESS' and key_2 == 'MATERIAL U.S. FEDERAL INCOME TAX CONSEQUENCES':
        return document[type_][sections[-2][1]:sections[-1][1]]
    else:
        return document[type_][sections[0][1]:sections[1][1]]

#clean S-1 form and save 'DESCRIPTION OF CERTAIN INDEBTEDNESS' section into .txt 
def get_key_text(s1):
    for doc,url in s1.items():
        key_1,key_2 = 'DESCRIPTION OF CERTAIN INDEBTEDNESS','DESCRIPTION OF CAPITAL STOCK'
        section = extract_section(doc,url,key_1,key_2)
        if not section:
            key_1,key_2 = 'DESCRIPTION OF OTHER INDEBTEDNESS','MATERIAL U.S. FEDERAL INCOME TAX CONSEQUENCES'
            section = extract_section(doc,url,key_1,key_2)
        
        soup = clean_soup(section)
        texts = soup.find_all(['p','dd'])
        tables = soup.find_all('tr')
        name = doc.replace('|','_').replace('/','-')
        
        #remove special characters
        text_strings = [i.text.replace('\xa0',' ').replace('Table of Contents','').
                            replace('\x92',"'").strip() for i in texts]
        #remove page number and empty string
        cleaned_strings = [i for i in text_strings if i != '' and not i.isdigit()]
        table_strings = [i.text.replace('\xa0',' ').strip() for i in tables]
        
        #store financial covenant tables in tables list
        tables = []
        for i in table_strings:
            date, ratio = i.split('   ')
            if 'Measurement Period' in i:
                table = PrettyTable(["Measurement Period Ending", "Ratio"])
                table.align["Measurement Period Ending"] = "r" # Right align city names
            if ratio[-1].isdigit():
                table.add_row([date,ratio])
            if 'thereafter' in date:
                tables.append(table)
        
        #combine tables and text      
        signal = 1
        table_count = 0
        for i in cleaned_strings:
            if signal == 1:                            #section title
                covenant = f'[{i.rstrip()}]\n'
                signal = 0
            else:
                if i[0].isupper() and len(i)<60:       #subtitles  
                    covenant += '\n\n<'+i.rstrip()+'>'
                elif i[0].isupper():                   #paragraphs
                    covenant += '\n\t'+i
                else:                                  #bullet points and other
                    if '·' in i:
                        covenant += '\n\t· '+i.replace('·','').strip()
                    else:
                        covenant += ' '+i
                if 'set forth below:' in i:            #table signal
                    covenant += '\n'+tables[table_count].get_string()
                    table_count += 1

        with open(f'{name}.txt','w',encoding="utf-8") as f:
            print(f'Writing to {name}...')
            for i in covenant.split('\n'):
                f.write(textwrap.fill(i,160)+'\n')
    print('Finished Writing!')