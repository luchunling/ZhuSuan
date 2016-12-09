#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
import sys
import os
import time

import tensorflow as tf
from tensorflow.contrib import layers
from six.moves import range
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from zhusuan.model import *
    from zhusuan.variational import nvil
    from zhusuan.evaluation import is_loglikelihood
except:
    raise ImportError()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import dataset
except:
    raise ImportError()


class M1:
    """
    The deep generative model used in variational autoencoder (VAE).

    :param n_z: A Tensor or int. The dimension of latent variables (z).
    :param n_x: A Tensor or int. The dimension of observed variables (x).
    :param n: A Tensor or int. The number of data, or batch size in mini-batch
        training.
    :param n_particles: A Tensor or int. The number of particles per node.
    """
    def __init__(self, n_z, n_x, n, n_particles, is_training):
        with StochasticGraph() as model:
            z_mean = tf.zeros([n_particles, n_z])
            z = Bernoulli(z_mean, sample_dim=1, n_samples=n)
            lx_z = layers.fully_connected(
                z.value, 500, normalizer_fn=layers.batch_norm,
                normalizer_params={'is_training': is_training,
                                   'updates_collections': None})
            lx_z = layers.fully_connected(
                lx_z, 500, normalizer_fn=layers.batch_norm,
                normalizer_params={'is_training': is_training,
                                   'updates_collections': None})
            lx_z = layers.fully_connected(lx_z, n_x, activation_fn=None)
            x = Bernoulli(lx_z)
        self.model = model
        self.x = x
        self.z = z
        self.n_particles = n_particles

    def log_prob(self, latent, observed, given):
        """
        The log joint probability function.

        :param latent: A dictionary of pairs: (string, Tensor).
        :param observed: A dictionary of pairs: (string, Tensor).
        :param given: A dictionary of pairs: (string, Tensor).

        :return: A Tensor. The joint log likelihoods.
        """
        z = latent['z']
        x = observed['x']
        x = tf.tile(tf.expand_dims(x, 0), [self.n_particles, 1, 1])
        z_out, x_out = self.model.get_output([self.z, self.x],
                                             inputs={self.z: z, self.x: x})
        log_px_z = tf.reduce_sum(x_out[1], -1)
        log_pz = tf.reduce_sum(z_out[1], -1)
        return log_px_z + log_pz


def q_net(x, n_z, n_particles, is_training):
    """
    Build the recognition network (Q-net) used as variational posterior.

    :param x: A Tensor.
    :param n_x: A Tensor or int. The dimension of observed variables (x).
    :param n_z: A Tensor or int. The dimension of latent variables (z).
    :param n_particles: A Tensor or int. Number of samples of latent variables.
    """
    with StochasticGraph() as variational:
        lz_x = layers.fully_connected(
            x, 500, normalizer_fn=layers.batch_norm,
            normalizer_params={'is_training': is_training,
                               'updates_collections': None})
        lz_x = layers.fully_connected(
            lz_x, 500, normalizer_fn=layers.batch_norm,
            normalizer_params={'is_training': is_training,
                               'updates_collections': None})
        lz_mean = layers.fully_connected(lz_x, n_z, activation_fn=None)
        z = Bernoulli(lz_mean, sample_dim=0, n_samples=n_particles)
    return variational, z


def baseline_net(x):
    lc_x = layers.fully_connected(x, 100)
    lc_x = layers.fully_connected(lc_x, 1, activation_fn=None)
    return lc_x

if __name__ == "__main__":
    tf.set_random_seed(1237)

    # Load MNIST
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'data', 'mnist.pkl.gz')
    x_train, t_train, x_valid, t_valid, x_test, t_test = \
        dataset.load_mnist_realval(data_path)
    x_train = np.vstack([x_train, x_valid]).astype('float32')
    np.random.seed(1234)
    x_test = np.random.binomial(1, x_test, size=x_test.shape).astype('float32')
    n_x = x_train.shape[1]

    # Define model parameters
    n_z = 40

    # Define training/evaluation parameters
    lb_samples = 1
    ll_samples = 50
    epoches = 3000
    batch_size = 100
    test_batch_size = 100
    iters = x_train.shape[0] // batch_size
    test_iters = x_test.shape[0] // test_batch_size
    test_freq = 10
    learning_rate = 0.001
    anneal_lr_freq = 200
    anneal_lr_rate = 0.75

    # Build the computation graph
    is_training = tf.placeholder(tf.bool, shape=[], name='is_training')
    learning_rate_ph = tf.placeholder(tf.float32, shape=[], name='lr')
    n_particles = tf.placeholder(tf.int32, shape=[], name='n_particles')
    x = tf.placeholder(tf.float32, shape=(None, n_x), name='x')
    n = tf.shape(x)[0]
    optimizer = tf.train.AdamOptimizer(learning_rate_ph, epsilon=1e-4)
    model = M1(n_z, n_x, n, n_particles, is_training)
    variational, lz = q_net(x, n_z, n_particles, is_training)
    z, z_logpdf = variational.get_output(lz)
    z_logpdf = tf.reduce_sum(z_logpdf, -1)

    cx = baseline_net(x)
    cost, lower_bound = nvil(
        model, {'x': x}, {'z': [z, z_logpdf]},
        baseline=cx, reduction_indices=0, variance_normalization=False)
    lower_bound = tf.reduce_mean(lower_bound)
    cost = tf.reduce_mean(cost)
    log_likelihood = tf.reduce_mean(is_loglikelihood(
        model, {'x': x}, {'z': [z, z_logpdf]}, reduction_indices=0))

    grads = optimizer.compute_gradients(cost)
    infer = optimizer.apply_gradients(grads)

    # train_writer = tf.train.SummaryWriter('/tmp/zhusuan',
    #                                       tf.get_default_graph())
    # train_writer.close()

    params = tf.trainable_variables()
    for i in params:
        print(i.name, i.get_shape())

    # Run the inference
    with tf.Session() as sess:
        sess.run(tf.initialize_all_variables())
        for epoch in range(1, epoches + 1):
            time_epoch = -time.time()
            if epoch % anneal_lr_freq == 0:
                learning_rate *= anneal_lr_rate
            np.random.shuffle(x_train)
            lbs = []
            for t in range(iters):
                x_batch = x_train[t * batch_size:(t + 1) * batch_size]
                x_batch = np.random.binomial(
                    n=1, p=x_batch, size=x_batch.shape).astype('float32')
                _, lb = sess.run([infer, lower_bound],
                                 feed_dict={x: x_batch,
                                            learning_rate_ph: learning_rate,
                                            n_particles: lb_samples,
                                            is_training: True})
                lbs.append(lb)
            time_epoch += time.time()
            print('Epoch {} ({:.1f}s): Lower bound = {}'.format(
                epoch, time_epoch, np.mean(lbs)))
            if epoch % test_freq == 0:
                time_test = -time.time()
                test_lbs = []
                test_lls = []
                for t in range(test_iters):
                    test_x_batch = x_test[
                        t * test_batch_size: (t + 1) * test_batch_size]
                    test_lb = sess.run(lower_bound,
                                       feed_dict={x: test_x_batch,
                                                  n_particles: lb_samples,
                                                  is_training: False})
                    test_ll = sess.run(log_likelihood,
                                       feed_dict={x: test_x_batch,
                                                  n_particles: ll_samples,
                                                  is_training: False})
                    test_lbs.append(test_lb)
                    test_lls.append(test_ll)
                time_test += time.time()
                print('>>> TEST ({:.1f}s)'.format(time_test))
                print('>> Test lower bound = {}'.format(np.mean(test_lbs)))
                print('>> Test log likelihood = {}'.format(np.mean(test_lls)))
