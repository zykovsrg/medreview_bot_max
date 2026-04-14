from __future__ import annotations

import unittest

from app.doc_parser import parse_google_document


class ParseGoogleDocumentTests(unittest.TestCase):
    def test_extracts_sections_and_skips_excluded_titles(self) -> None:
        document = {
            "documentId": "doc-1",
            "title": "Тестовая статья",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Тестовая статья\n"}}],
                            "paragraphStyle": {"namedStyleType": "HEADING_1"},
                        }
                    },
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Вступление статьи\n"}}],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    },
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Причины\n"}}],
                            "paragraphStyle": {"namedStyleType": "HEADING_2"},
                        }
                    },
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Текст раздела причины\n"}}],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    },
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Источники\n"}}],
                            "paragraphStyle": {"namedStyleType": "HEADING_2"},
                        }
                    },
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Источник 1\n"}}],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    },
                ]
            },
        }

        result = parse_google_document(
            document,
            "https://docs.google.com/document/d/doc-1/edit",
            excluded_titles=("Источники",),
        )

        self.assertEqual(result.title, "Тестовая статья")
        self.assertEqual(result.intro, "Вступление статьи")
        self.assertEqual(len(result.sections), 1)
        self.assertEqual(result.sections[0].title, "Причины")
        self.assertEqual(result.sections[0].body, "Текст раздела причины")


if __name__ == "__main__":
    unittest.main()

