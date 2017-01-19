#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import division

import tensorflow as tf
import numpy as np
import pytest
import scipy
import scipy.stats

from .context import zhusuan
from zhusuan.mcmc.nuts import NUTS
from zhusuan.mcmc.base_hmc import BaseHMC
from zhusuan.mcmc.hmc import HMC


def test_nuts():
    x = tf.Variable(tf.zeros([1]))
    log_likelihood = -0.5 * tf.square(x)

    # Data definition
    data = {}

    sess = tf.Session()
    sess.run(tf.initialize_all_variables())

    n_samples = 1000
    burnin = 500
    sampler = NUTS(sess, data, [x], log_likelihood, m_adapt=burnin,
                   mass=[np.array([1])], mass_adaptation=True)
    sampler = NUTS(sess, data, [x], log_likelihood, m_adapt=burnin,
                   mass_adaptation=True)

    # For coverage of sample_work virtual function in BaseHMC
    BaseHMC.sample_work(sampler)

    for i in range(n_samples):
        sampler.sample()

    samples = np.squeeze(sampler.models)
    p_value = scipy.stats.normaltest(samples)
    assert(p_value > 0.1)

    sampler.stat(burnin)


def test_hmc():
    x = tf.zeros([])



    def log_posterior(latent, observed=None, given=None):
        x = latent['x']
        return -0.5 * tf.square(x)

    n_samples = 10000
    sampler = HMC(step_size=0.1)
    mass = [tf.ones([])]
    sample_step, _, _, _, _, _ = sampler.sample(
        log_posterior, None, {'x': x}, 0, mass=mass)

    with tf.Session() as sess:
        sess.run(tf.initialize_all_variables())
        samples = []
        for i in range(n_samples):
            samples.append(sess.run(sample_step))

    samples = np.squeeze(np.array(samples), 1)[5000:]
    print(np.mean(samples))
    print(np.var(samples))
    p_value = scipy.stats.normaltest(samples)
    assert p_value > 0.1
    import matplotlib.pyplot as plt
    plt.hist(samples, bins=50)
    plt.show()
