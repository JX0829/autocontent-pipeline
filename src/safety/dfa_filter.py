import os
import re

class DFAFilter:
    def __init__(self, dict_path="./config/sensitive_words.txt"):
        self.root = {}
        self.end_key = "\x00"
        self.load_words(dict_path)

    def load_words(self, dict_path):
        if not os.path.exists(dict_path):
            return
        with open(dict_path, "r", encoding="utf-8") as f:
            for line in f:
                word = line.strip().lower()
                if not word or word.startswith("#"):
                    continue
                self.add_word(word)

    def add_word(self, word):
        node = self.root
        for char in word:
            if char not in node:
                node[char] = {}
            node = node[char]
        node[self.end_key] = True

    def contains_sensitive(self, text: str) -> tuple[bool, str]:
        if not text:
            return False, ""
        lower_text = text.lower()
        length = len(lower_text)
        for i in range(length):
            node = self.root
            j = i
            while j < length and lower_text[j] in node:
                node = node[lower_text[j]]
                if self.end_key in node:
                    return True, text[i:j+1]
                j += 1
        return False, ""

    @staticmethod
    def sanitize_upstream_text(text: str) -> str:
        if not text:
            return ""
        # 剥离隐藏 HTML 注释与危险脚本标签
        clean = re.sub(r'<!--[\s\S]*?-->', '', text)
        clean = re.sub(r'<script[\s\S]*?</script>', '', clean, flags=re.I)
        clean = re.sub(r'<style[\s\S]*?</style>', '', clean, flags=re.I)
        # 过滤可疑的 Prompt 劫持词
        clean = re.sub(r'(?i)(ignore previous instructions|忽略以上指令|系统指令)', '[FILTERED]', clean)
        return clean.strip()
