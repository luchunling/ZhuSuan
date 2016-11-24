#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import division

import tensorflow as tf
from scipy import misc
import numpy as np
import pytest

from .context import zhusuan
from zhusuan.utils import *


def test_log_sum_exp():
    with tf.Session() as sess:
        a = np.array([[[1., 3., 0.2], [0.7, 2., 1e-6]],
                      [[0., 1e6, 1.], [1., 1., 1.]]])
        for keepdims in [True, False]:
            true_values = misc.logsumexp(a, (0, 2), keepdims=keepdims)
            test_values = sess.run(log_sum_exp(
                tf.constant(a), (0, 2), keepdims))
            assert (np.abs((test_values - true_values) / true_values).max() <
                    1e-6)


def test_log_mean_exp():
    with tf.Session() as sess:
        a = np.array([[[1., 3., 0.2], [0.7, 2., 1e-6]],
                      [[0., 1e6, 1.], [1., 1., 1.]]])
        for keepdims in [True, False]:
            true_values = misc.logsumexp(a, (0, 2), keepdims=keepdims) - \
                          np.log(a.shape[0] * a.shape[2])
            test_values = sess.run(log_mean_exp(
                tf.constant(a), (0, 2), keepdims))
            assert (np.abs((test_values - true_values) / true_values).max() <
                    1e-6)

        b = np.array([[0., 1e-6, 10.1]])
        test_values = sess.run(log_mean_exp(b, 0, keep_dims=False))
        assert (np.abs(test_values - b).max() < 1e-6)


def test_as_tensor():
    as_tensor(1.)
    as_tensor([1, 2])
    as_tensor([[1., 2.]])
    as_tensor(np.ones((3, 4)))
    as_tensor(tf.ones((5, 1)))
    as_tensor(tf.Variable(np.zeros(3)))
    as_tensor(tf.placeholder(tf.float32, shape=(3, 4)))
    with pytest.raises(TypeError):
        as_tensor(tf.SparseTensor(indices=[[0, 0], [1, 2]],
                                  values=[1, 2], shape=[3, 4]))


def test_ensure_dim_match():
    a, b = ensure_dim_match([tf.placeholder(tf.float32, (None, 1, 3)),
                             tf.placeholder(tf.float32, (None, 5, 3))], 1)
    assert(a.get_shape().as_list() == [None, 5, 3])
    assert(b.get_shape().as_list() == [None, 5, 3])

    c, d = ensure_dim_match([tf.placeholder(tf.float32, (1, 3)),
                             tf.placeholder(tf.float32, (None, 3))], 0)
    assert(c.get_shape().as_list() == [None, 3])
    assert(d.get_shape().as_list() == [None, 3])

    with tf.Session() as sess:
        test_values = sess.run(
            ensure_dim_match([tf.ones(1)], 0))[0]
        assert(test_values.shape == (1,))

        test_values, _ = sess.run(
            ensure_dim_match([tf.ones(1), tf.ones(5)], 0))
        assert(test_values.shape == (5,))

        test_values, _ = sess.run(
            ensure_dim_match([tf.ones((3, 1)), tf.ones((3, 5))], 1))
        assert(test_values.shape == (3, 5))

    with tf.Session() as sess:
        with pytest.raises(tf.errors.InvalidArgumentError):
            sess.run(ensure_dim_match([tf.ones((2, 3)), tf.ones((5, 3))], 0))


# def test_ensure_dim_match_gradients():
#     a = tf.ones((2, 1, 2))
#     b = tf.ones((2, 5, 2))
#     a_, b_ = ensure_dim_match([a, b], 1)
#     grad = tf.gradients(a_, a)[0]
#     with tf.Session() as sess:
#         assert(sess.run(tf.shape(grad)).tolist() == [2, 1, 2])


def test_add_name_scope():
    class A:
        @add_name_scope
        def f(self):
            return tf.ones(1)

    a = A()
    node = a.f()
    assert(node.name == 'A.f/ones:0')


def test_copy():
    def list_diff(a, b):
        for i in range(2):
            if (a[i] != b[i]).any():
                return False
        return True

    a = [np.array([1, 2]), np.array([[3, 4], [5, 6]])]
    c = [np.array([1, 2]), np.array([[3, 4], [5, 6]])]
    b = copy(a)
    assert(list_diff(a, b))

    b[0][0] = 10
    assert(list_diff(a, c))


def test_mean_statistics():
    ma = MeanStatistics()
    assert(ma.mean() == 0)
    ma.add(1.0)
    ma.add(2.0)
    assert(ma.mean() == 1.5)

    mb = MeanStatistics(shape=(2))
    mb.add(np.array([1, 2]))
    mb.add(np.array([3, 4]))
    assert((mb.mean() == np.array([2, 3])).all())


def test_variance_estimator():
    shape = [100]
    n = np.random.normal(size=(100, 100))

    est = VarianceEstimator([shape])
    for i in range(100):
        est.add([n[i, :]])

    npvar = np.var(n, axis=0) * 100 / 99
    estvar = est.variance()[0]
    assert((npvar - estvar < 1e-7).all())

    est2 = VarianceEstimator(shape=[(1)])
    assert(est2.variance()[0] == 0)


def test_if_raise():
    try:
        if_raise(True, RuntimeError("exception"))
    except RuntimeError:
        pass
