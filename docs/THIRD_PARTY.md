# 第三方数据

## JLPT 词表

`src/nihongo/data/jlpt_vocab.tsv` 由 [open-anki-jlpt-decks](https://github.com/jamsinclair/open-anki-jlpt-decks) 的 `src/n1.csv`–`src/n5.csv` 合并而成（同一单词出现在多个级别时保留较低级别），只保留单词、读音、英文释义和级别。

```
MIT License

Copyright (c) 2020 Jamie Sinclair

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

该项目的原始数据来自 [chyyran/jlpt-anki-decks](https://github.com/chyyran/jlpt-anki-decks)，最初基于 tanos.co.uk 的词表。JLPT 官方自 2010 年起不再公布考试词表，这些级别划分是社区整理的参考。
