import datetime as dt

# import pdb
import scrapy
from scrapy.http import FormRequest

from gazette.items import Gazette
from gazette.spiders.base import BaseGazetteSpider
from gazette.utils.dates import daily_sequence


# GLOBAL URLS USED BY ALL METHODS
old_url = "https://sistemas.canoas.rs.gov.br/gt/publico/dof/index.jsf"
new_url = "https://sistemas.canoas.rs.gov.br/domc/pesquisar?publication_date="
headers = {
    "Accept": "application/xml, text/xml, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Faces-Request": "partial/ajax",
    "X-Requested-With": "XMLHttpRequest",
}


class UFMunicipioSpider(BaseGazetteSpider):
    name = "rs_canoas"
    TERRITORY_ID = "4304606"
    allowed_domains = ["sistemas.canoas.rs.gov.br"]
    start_urls = ["https://sistemas.canoas.rs.gov.br/domc/pesquisar?"]
    start_date = dt.date(2012, 11, 5)
    end_date = None

    def start_requests(self):
        if self.end_date is None:
            self.end_date = self.start_date

        for d in daily_sequence(self.start_date, self.end_date, "%Y-%m-%d"):
            day = dt.datetime.strptime(d, "%Y-%m-%d").date()

            # OLD SYSTEM: used for publications up to May 29, 2018.
            if day <= dt.date(2018, 5, 29):
                yield scrapy.Request(url=old_url, callback=self.parse)

            # BUG - Spider always processes all gazettes up to the current date.
            elif day >= dt.date(2018, 5, 30):
                url = new_url + day.strftime("%d/%m/%Y")

                if self.end_date != self.start_date:
                    url = (
                        url
                        + "&publication_final_date="
                        + self.end_date.strftime("%d/%m/%Y")
                    )

                # breakpoint()

            yield scrapy.Request(url=url)

    def parse(self, response):
        # SELECT OLD OR NEW URL
        uri = response.url

        if old_url in uri:
            # OLD SYSTEM - STEP 1: OPEN HOME PAGE AND TRIGGER SEARCH
            # TODO - Spider needs to process the full interval between start and end dates.
            view_state = response.css(
                'input[name="javax.faces.ViewState"]::attr(value)'
            ).get()

            data = {
                "javax.faces.partial.ajax": "true",
                "javax.faces.source": "j_idt35:j_idt50",
                "javax.faces.partial.execute": "@all",
                "javax.faces.partial.render": "j_idt35 colunaDireita growl",
                "j_idt35:j_idt50": "j_idt35:j_idt50",
                "j_idt35": "j_idt35",
                "j_idt35:dataPublicacao1_input": self.start_date.strftime("%d/%m/%Y"),
                "j_idt35:tipoPublicacao1_focus": "",
                "j_idt35:tipoPublicacao1_input": "Todos",
                "j_idt35:palavrasChave1": "",
                "javax.faces.ViewState": view_state,
            }

            # Create a local copy of the headers to avoid asynchronous scope leakage.
            req_headers = headers.copy()
            req_headers["Referer"] = uri

            yield FormRequest(
                url=uri,
                formdata=data,
                headers=req_headers,
                callback=self.parse_table_sections,
            )

        elif new_url in uri:
            # NEW SYSTEM - SINGLE-STEP SEARCH
            vector = {}
            fileshare = "https://sistemas.canoas.rs.gov.br/domc/api/edition-file/"
            date_pattern = "([0-9]{2}/[0-9]{2}/[0-9]{4})"
            edition_pattern = r"Edição.*\b(\d+)"
            download_pattern = r"setPage\((\d+)"

            # ALL PAGINATION PAGES
            pages = response.xpath("//ul[@class='pagination']")

            # PROCESS EACH PAGE
            for page in pages.xpath("li['@a href']"):
                # NEXT PAGE
                if page.css("a::attr(rel)"):
                    pg_url = page.css("a::attr(href)").get()
                    yield scrapy.Request(url=pg_url, callback=self.parse)

                # ALL RECORDS IN THE CURRENT PAGE
                editions = response.css(".table-bordered")
                key = 0
                extra_num = ""
                endpoint = ""

                # PROCESS EACH RECORD IN THE PAGE
                for edition in editions.xpath("//tbody/tr"):
                    # IDENTIFY UNIQUE EDITIONS
                    extra = False
                    edition_number = edition.re(edition_pattern, edition)
                    edition_number = "".join(edition_number)

                    # CHECK WHETHER THE EDITION IS A SUPPLEMENTARY EDITION
                    if edition.re(r"Complementar", edition):
                        extra = True
                        extra_regex = edition.re(r"\s\d\s", edition)

                        if extra_num == extra_regex:
                            continue

                        extra_num = extra_regex

                    publication_date = edition.re(date_pattern, edition)
                    publication_date = dt.datetime.strptime(
                        "".join(publication_date), "%d/%m/%Y"
                    ).date()

                    # AVOID REPEATED DOWNLOADS
                    download = edition.re_first(download_pattern)

                    if endpoint == download:
                        continue

                    if extra is False:
                        endpoint = download

                    # BUILD THE DOWNLOAD URL
                    file_url = fileshare + download
                    key += 1

                    # STORE ALL VALID RECORDS
                    vector[key] = {
                        "date": publication_date,
                        "edition_number": edition_number,
                        "is_extra_edition": extra,
                        "file_urls": [file_url],
                        "power": "executive",
                    }

            # RETURN ONE GAZETTE RECORD AT A TIME
            for idx in vector.items():
                yield Gazette(idx[1])

    def parse_table_sections(self, response):
        # OLD SYSTEM - STEP 2: SELECT THE TABLE SECTION ROW
        inner_html = response.xpath(
            '//update[contains(text(), "j_idt73")]/text()'
        ).get()

        if not inner_html:
            self.logger.error("Unable to find the table component in the response.")
            return

        seletor = scrapy.Selector(text=inner_html)

        view_state = response.xpath(
            '//update[@id="javax.faces.ViewState"]/text()'
        ).get()

        primary_row_key = seletor.xpath("//tr[@data-rk]/@data-rk").get()

        if not primary_row_key:
            self.logger.error("No section/row was found in the table for this day.")
            return

        data_row_click = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "j_idt73:j_idt74",
            "javax.faces.partial.execute": "j_idt73:j_idt74",
            "javax.faces.partial.render": "colunaDireita",
            "javax.faces.behavior.event": "rowSelect",
            "javax.faces.partial.event": "rowSelect",
            "j_idt73:j_idt74_instantSelectedRowKey": primary_row_key,
            "j_idt73": "j_idt73",
            "j_idt73:j_idt74_selection": primary_row_key,
            "javax.faces.ViewState": view_state,
        }

        req_headers = headers.copy()
        req_headers["Referer"] = response.url

        yield FormRequest(
            url=response.url,
            formdata=data_row_click,
            headers=req_headers,
            callback=self.parse_transition_page,
            dont_filter=True,
        )

    def parse_transition_page(self, response):
        # OLD SYSTEM - STEP 3: SELECT THE UNIQUE DOM FILE BUTTON
        # TO REACH THE FINAL PAGE
        view_state = response.xpath(
            '//update[@id="javax.faces.ViewState"]/text()'
        ).get()

        payload_download = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "j_idt90:j_idt98",
            "javax.faces.partial.execute": "@all",
            "javax.faces.partial.render": "colunaDireita",
            "j_idt90:j_idt98": "j_idt90:j_idt98",
            "j_idt90": "j_idt90",
            "j_idt90:publicacoes_focus": "",
            "j_idt90:publicacoes_input": "",
            "javax.faces.ViewState": view_state,
        }

        req_headers = headers.copy()
        req_headers["Referer"] = response.url

        yield FormRequest(
            url=response.url,
            formdata=payload_download,
            headers=req_headers,
            callback=self.parse_download_buttom,
            dont_filter=True,
        )

    def parse_download_buttom(self, response):
        # OLD SYSTEM - STEP 4: EXTRACT THE DIRECT GAZETTE FILE URL
        # (END OF FLOW)
        inner_html = response.xpath(
            '//update[contains(text(), "j_idt109")]/text()'
        ).get()

        if not inner_html:
            self.logger.error("Unable to extract the inner HTML from the final screen.")
            return

        seletor = scrapy.Selector(text=inner_html)

        # 1. Extract the relative PDF link from the <object> tag in the XML.
        link_relativo = seletor.xpath('//object[@type="application/pdf"]/@data').get()

        if not link_relativo:
            self.logger.error("PDF link not found inside the HTML object.")
            return

        # 2. Convert the relative link into an absolute URL.
        #    response.urljoin() also handles HTML entities such as &amp;.
        url_direta_pdf = response.urljoin(link_relativo)

        self.logger.info(f"Direct URL sent to the Gazette pipeline: {url_direta_pdf}")

        # 3. Return the final item expected by the Querido Diário framework.
        yield Gazette(
            date=self.start_date,
            file_urls=[url_direta_pdf],
            is_extra_edition=False,
            power="executive",
        )
