# -*- coding: utf-8 -*-
from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
import sys
import os
import time

import tensorflow as tf
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zhusuan.mcmc.hmc2 import HMC
from matplotlib import pyplot as plt
import scipy
import scipy.stats
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from zhusuan.model import *
except:
    raise ImportError()

tf.set_random_seed(1)

kernel_width = 0.1
num_samples = 100
num_chains = 1000

with StochasticGraph() as model:
    lx = Normal(tf.zeros((num_chains)), tf.zeros((num_chains)))


def log_posterior(latent, observed, given):
    _, log_p = model.get_output(lx, inputs={lx: tf.identity(latent['x'])})
    return log_p

hmc = HMC(step_size=0.3, n_leapfrogs=5)

x = tf.Variable(tf.zeros((num_chains)), name='x')
sampler = hmc.sample(log_posterior, {}, {'x': x}, chain_axis=0)

sess = tf.Session()
params = tf.trainable_variables()
for i in params:
    print(i.name, i.get_shape())
sess.run(tf.global_variables_initializer())

train_writer = tf.summary.FileWriter('train', tf.get_default_graph())
train_writer.close()

samples = []
print('Sampling...')
for i in range(num_samples):
    q, p, oh, nh, ar = sess.run(sampler)
    #print(q, p, oh, nh, ar)
    if isinstance(q[0], np.ndarray):
        samples.extend(list(q[0]))
    else:
        samples.append(q[0])
print('Finished.')


def kde(xs, mu, batch_size):
    mu_n = len(mu)
    assert(mu_n % batch_size == 0)
    xs_row = np.expand_dims(xs, 1)
    ys = np.zeros(xs.shape)

    for b in range(mu_n // batch_size):
        mu_col = np.expand_dims(mu[b*batch_size:(b+1)*batch_size], 0)
        ys += (1 / np.sqrt(2 * np.pi) / kernel_width) * \
             np.mean(np.exp((-0.5 / kernel_width ** 2) * np.square(xs_row - mu_col)), 1)

    ys /= (mu_n / batch_size)
    return ys

xs = np.linspace(-5, 5, 1000)
ys = kde(xs, np.array(samples), num_chains)

f, ax = plt.subplots()
ax.plot(xs, ys)
ax.plot(xs, scipy.stats.norm.pdf(xs))

#print(samples)
print(scipy.stats.normaltest(samples))

plt.show()
