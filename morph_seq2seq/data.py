#! /usr/bin/env python
# -*- coding: utf-8 -*-
# vim:fenc=utf-8
#
# Copyright © 2018 Judit Acs <judit@sch.bme.hu>
#
# Distributed under terms of the MIT license.

from collections import defaultdict
from sys import stdin
import numpy as np

import torch
from torch.autograd import Variable


use_cuda = torch.cuda.is_available()


class Dataset(object):
    CONSTANTS = {
        'PAD': 0,
        'SOS': 1,
        'EOS': 2,
        'UNK': 3,
    }

    def __init__(self, cfg, stream=None):
        self.cfg = cfg
        self.src_vocab = defaultdict(lambda: len(self.src_vocab))
        if self.cfg.share_vocab:
            self.tgt_vocab = self.src_vocab
        else:
            self.tgt_vocab = defaultdict(lambda: len(self.tgt_vocab))
        for k, v in Dataset.CONSTANTS.items():
            self.src_vocab[k] = v
            self.tgt_vocab[k] = v
        self.load_data_from_stream(stream=stream)
        self.update_config()

    def update_config(self):
        self.cfg.input_size = len(self.src_vocab)
        self.cfg.output_size = len(self.tgt_vocab)

    def src_lookup(self, ch, frozen=False):
        if frozen is False:
            return self.src_vocab[ch]
        return self.src_vocab.get(ch, Dataset.CONSTANTS['UNK'])

    def tgt_reverse_lookup(self, idx):
        try:
            self.tgt_inv_vocab
        except AttributeError:
            self.tgt_inv_vocab = {
                i: ch for ch, i in self.tgt_vocab.items()}
        return self.tgt_inv_vocab.get(idx, '<UNK>')

    def load_data_from_stream(self, stream=stdin, frozen_vocab=False):
        self.samples = []
        self.raw_samples = []
        for line in stream:
            src, tgt = line.rstrip('\n').split('\t')
            src = src.split(' ')
            tgt = tgt.split(' ')
            self.raw_samples.append((src, tgt))
        self.src_maxlen = max(len(s[0]) for s in self.raw_samples)
        self.tgt_maxlen = max(len(s[1]) for s in self.raw_samples)
        self.src_seqlen = [len(s[0]) for s in self.raw_samples]
        self.tgt_seqlen = [len(s[1]) + 1 for s in self.raw_samples]
        PAD = Dataset.CONSTANTS['PAD']
        EOS = Dataset.CONSTANTS['EOS']
        UNK = Dataset.CONSTANTS['UNK']
        for src, tgt in self.raw_samples:
            if frozen_vocab is True:
                self.samples.append((
                    [self.src_vocab.get(c, UNK) for c in src] +
                    [PAD] * (self.src_maxlen-len(src)),
                    [self.tgt_vocab.get(c, UNK) for c in tgt] + [EOS] +
                    [PAD] * (self.tgt_maxlen-len(tgt)),
                ))
            else:
                self.samples.append((
                    [self.src_vocab[c] for c in src] +
                    [PAD] * (self.src_maxlen-len(src)),
                    [self.tgt_vocab[c] for c in tgt] + [EOS] +
                    [PAD] * (self.tgt_maxlen-len(tgt)),
                ))
        self.tgt_maxlen += 1

    def get_random_batch(self, batch_size):
        idx = np.random.choice(range(len(self.samples)), batch_size)
        idx = sorted(idx, key=lambda i: -self.src_seqlen[i])
        src = [self.samples[i][0] for i in idx]
        tgt = [self.samples[i][1] for i in idx]
        src = Variable(torch.LongTensor(src)).transpose(0, 1)
        src = src.cuda() if use_cuda else src
        tgt = Variable(torch.LongTensor(tgt)).transpose(0, 1)
        tgt = tgt.cuda() if use_cuda else tgt
        src_len = [self.src_seqlen[i] for i in idx]
        tgt_len = [self.tgt_seqlen[i] for i in idx]
        tgt_len = Variable(torch.LongTensor(tgt_len))
        tgt_len = tgt_len.cuda() if use_cuda else tgt_len
        return src, tgt, src_len, tgt_len

    def batched_iter(self, batch_size):
        batch_count = len(self.samples) // batch_size
        if batch_count * batch_size < len(self.samples):
            batch_count += 1
        for i in range(0, batch_count):
            start = i * batch_size
            end = min((i+1) * batch_size, len(self.samples))
            batch = self.samples[start:end]
            src = [s[0] for s in batch]
            tgt = [s[1] for s in batch]
            src_len = [self.src_seqlen[bi] for bi in range(start, end)]
            tgt_len = [self.tgt_seqlen[bi] for bi in range(start, end)]

            batch = zip(src, tgt, src_len, tgt_len)
            batch = sorted(batch, key=lambda x: -x[2])

            src, tgt, src_len, tgt_len = zip(*batch)

            src = Variable(torch.LongTensor(src)).transpose(0, 1)
            tgt = Variable(torch.LongTensor(tgt)).transpose(0, 1)
            tgt_len = Variable(torch.LongTensor(tgt_len))

            if use_cuda:
                src = src.cuda()
                tgt = tgt.cuda()
                tgt_len = tgt_len.cuda()

            yield src, tgt, src_len, tgt_len


class ValidationDataset(Dataset):
    def __init__(self, train_data, stream):
        self.cfg = train_data.cfg
        self.src_vocab = train_data.src_vocab
        self.tgt_vocab = train_data.tgt_vocab
        self.load_data_from_stream(frozen_vocab=True, stream=stream)


class InferenceDataset(Dataset):
    def __init__(self, cfg, stream):
        self.cfg = cfg
        self.load_src_vocab()
        self.load_data_from_stream(stream=stream)

    def load_src_vocab(self):
        with open(self.cfg.src_vocab_file) as f:
            self.src_vocab = {}
            for l in f:
                src, tgt = l.rstrip("\n").split("\t")
                self.src_vocab[src] = tgt

    def load_data_from_stream(self, stream=stdin):
        self.samples = []
        self.raw_samples = [l.rstrip("\n") for l in stream]
        self.len_mapping, self.samples = zip(*sorted(
            enumerate(self.raw_samples), key=lambda x: -len(x[1])))