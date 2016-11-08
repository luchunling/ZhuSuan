
# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import division
import tensorflow as tf
import numpy as np
import math
import os
from dataset import load_uci_german_credits, load_binary_mnist_realval
from zhusuan.optimization.gradient_descent_optimizer import \
    GradientDescentOptimizer
from zhusuan.distributions import norm, bernoulli
from zhusuan.mcmc.sgld import SGLD
from zhusuan.diagnostics import ESS

float_eps = 1e-30

tf.set_random_seed(0)

# Load MNIST dataset
n =50000
n_dims = 784
minibatch_size = 500
R = n/minibatch_size
mu = 0
sigma = 1./math.sqrt(n)

data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'data', 'mnist.pkl.gz')
X_train, y_train, _, _, X_test, y_test = load_binary_mnist_realval(data_path)
X_train = X_train[:n] * 256
y_train = y_train[:n]
X_test = X_test * 256

# Load German credits dataset
# n = 900
# n_dims = 24
# mu = 0
# sigma = 1./math.sqrt(n)
#
# data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
#                          'data', 'german.data-numeric')
# X_train, y_train, X_test, y_test = load_uci_german_credits(data_path, n)

# Define graph
# Data
x_input = tf.placeholder(tf.float32, [None, n_dims], name='x_input')
x = tf.Variable(tf.zeros((n, n_dims)), trainable=False, name='x')
y = tf.placeholder(tf.float32, [None], name='y')
update_data = tf.assign(x, x_input, validate_shape=False, name='update_data')
x_input_minibatch = tf.placeholder(tf.float32, [None, n_dims], name='x_input_minibatch')
x_minibatch = tf.Variable(tf.zeros((minibatch_size, n_dims)), trainable=False, name='x_minibatch')
y_minibatch = tf.placeholder(tf.float32, [None], name='y_minibatch')
update_data_minibatch = tf.assign(x_minibatch, x_input_minibatch, validate_shape=False, name='update_data_minibatch')
ratio = tf.placeholder(tf.float32, [])
# Model
beta = tf.Variable(np.zeros(n_dims), dtype=tf.float32, name='beta')
vars = [beta]
r = tf.Variable(np.zeros(n_dims), dtype=tf.float32, name='r')
squared_learning_rate = [r]

def log_prior(var_list):
    beta = var_list[0]
    log_prior = tf.reduce_sum(norm.logpdf(beta, 0, sigma))
    return log_prior

def log_likelihood(var_list):
    beta = var_list[0]
    scores = tf.reduce_sum(x_minibatch * beta, reduction_indices=(1,))
    logits = tf.nn.sigmoid(scores)
    log_likelihood = tf.reduce_sum(bernoulli.logpdf(y_minibatch, logits))
    return log_likelihood

def log_likelihood_all(var_list):
    beta = var_list[0]
    scores = tf.reduce_sum(x * beta, reduction_indices=(1,))
    logits = tf.nn.sigmoid(scores)
    log_likelihood_all = tf.reduce_sum(bernoulli.logpdf(y, logits))
    return log_likelihood_all

def log_joint(var_list):
    return log_prior(var_list)+log_likelihood_all(var_list)

# Evaluate
scores = tf.reduce_sum(x * beta, reduction_indices=(1,))
logits = tf.nn.sigmoid(scores)
predictions = tf.cast(logits > 0.5, tf.float32)
n_correct = tf.reduce_sum(predictions * y + (1 - predictions) * (1 - y))
get_log_joint = tf.reduce_sum(norm.logpdf(beta, 0, sigma)) + \
                tf.reduce_sum(bernoulli.logpdf(y, logits))

# Sampler
sampler = SGLD(sample_threshold=10^-2, a=1.26e-10, b=0.023,
               gamma=0.1, global_learning_rate=8e-5)
sample_step, step_size = sampler.sample(log_likelihood, log_prior, vars, ratio,
            minibatch_size, squared_learning_rate, RMS_decay_rate=0.8, Ada=True)

# Session
sess = tf.Session()

# Find a MAP solution
sess.run(tf.initialize_all_variables())
sess.run(update_data, feed_dict={x_input: X_train})

# optimizer = GradientDescentOptimizer(sess, {y: y_train}, -get_log_joint,
#                                      vars, stepsize_tol=1e-9, tol=1e-7)
# optimizer.optimize()

chain_length = 100
burnin = 50

sample_sum = []
num_samples = chain_length - burnin
train_scores = np.zeros((X_train.shape[0]))
test_scores = np.zeros((X_test.shape[0]))

all_samples = []

for i in range(chain_length):
    #Feed data in
    r = np.random.permutation(X_train.shape[0])
    X_train = X_train[r, :]
    y_train = y_train[r]
    sess.run(update_data, feed_dict={x_input: X_train})
    for j in range(int(R)):
        sess.run(update_data_minibatch, feed_dict={x_input_minibatch: X_train[j*minibatch_size:(j+1)*minibatch_size]})
        model, ss = sess.run([sample_step, step_size],
                                  feed_dict={y_minibatch: y_train[j*minibatch_size:(j+1)*minibatch_size], ratio: R})
        if j==1:
            print(np.sqrt(n_dims/np.sum(ss,axis=1)))
    #Compute model sum
    if i == burnin:
        sample_sum = model
    elif i > burnin:
        for j in range(len(model)):
            sample_sum[j] += model[j]
    if i >= burnin:
        all_samples.append(model)

    # evaluate
    n_train_c, train_pred_c, lj = sess.run(
        (n_correct, logits, get_log_joint), feed_dict={y: y_train})
    sess.run(update_data, feed_dict={x_input: X_test})
    n_test_c, test_pred_c = sess.run((n_correct, logits),
                                     feed_dict={y: y_test})
    print('Log likelihood = %f, Train set accuracy = %f, '
          'test set accuracy = %f' %
          (lj, (float(n_train_c) / X_train.shape[0]),
           (float(n_test_c) / X_test.shape[0])))

    # Accumulate scores
    if i >= burnin:
        train_scores += train_pred_c
        test_scores += test_pred_c

all_samples = np.squeeze(np.array(all_samples))

# Gibbs classifier
train_scores /= num_samples
test_scores /= num_samples

train_pred = (train_scores > 0.5).astype(np.float32)
test_pred = (test_scores > 0.5).astype(np.float32)

train_accuracy = float(np.sum(train_pred == y_train)) / X_train.shape[0]
test_accuracy = float(np.sum(test_pred == y_test)) / X_test.shape[0]

# Expected classifier
# Compute mean
set_mean = []
for j in range(len(vars)):
    set_mean.append(vars[j].assign(sample_sum[j] / num_samples))
sess.run(set_mean)

# Test expected classifier
sess.run(update_data, feed_dict={x_input: X_train})
r_log_likelihood = sess.run(get_log_joint, feed_dict={y: y_train})
n_train_c = sess.run(n_correct, feed_dict={y: y_train})
sess.run(update_data, feed_dict={x_input: X_test})
n_test_c = sess.run(n_correct, feed_dict={y: y_test})

print('Log likelihood of expected parameters: %f, train set accuracy = %f, '
      'test set accuracy = %f' %
      (r_log_likelihood, (float(n_train_c) / X_train.shape[0]),
       (float(n_test_c) / X_test.shape[0])))
print('Gibbs classifier: train set accuracy = %f, test set accuracy = %f'
      % (train_accuracy, test_accuracy))

print('ESS = {}'.format(ESS(all_samples, burnin=0)))
