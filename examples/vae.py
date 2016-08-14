#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
import sys
import os
import time

import tensorflow as tf
import prettytensor as pt
from six.moves import range
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from zhusuan.distributions import norm, bernoulli
    from zhusuan.utils import log_mean_exp
    from zhusuan.variational import ReparameterizedNormal, advi
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

    :param n_z: Int. The dimension of latent variables (z).
    :param n_x: Int. The dimension of observed variables (x).
    """
    def __init__(self, n_z, n_x):
        self.n_z = n_z
        self.n_x = n_x
        with pt.defaults_scope(activation_fn=tf.nn.relu,
                               scale_after_normalization=True):
            self.l_x_z = (pt.template('z').
                          fully_connected(500).
                          batch_normalize().
                          fully_connected(500).
                          batch_normalize().
                          fully_connected(n_x, activation_fn=tf.nn.sigmoid))

    def log_prob(self, z, x):
        """
        The joint likelihood of M1 deep generative model.

        :param z: Tensor of shape (batch_size, samples, n_z). n_z is the
            dimension of latent variables.
        :param x: Tensor of shape (batch_size, n_x). n_x is the dimension of
            observed variables (data).

        :return: A Tensor of shape (batch_size, samples). The joint log
            likelihoods.
        """
        l_x_z = self.l_x_z.construct(
            z=tf.reshape(z, ((-1, self.n_z)))).reshape(
            (-1, int(z.get_shape()[1]), self.n_x)).tensor
        log_px_z = tf.reduce_sum(
            bernoulli.logpdf(tf.expand_dims(x, 1), l_x_z, eps=1e-6), 2)
        log_pz = tf.reduce_sum(norm.logpdf(z), 2)
        return log_px_z + log_pz


def q_net(x, n_z):
    """
    Build the recognition network (Q-net) used as variational posterior.

    :param x: Tensor of shape (batch_size, n_x).
    :param n_z: Int. The dimension of latent variables (z).

    :return: A Tensor of shape (batch_size, n_z). Variational mean of latent
        variables.
    :return: A Tensor of shape (batch_size, n_z). Variational log standard
        deviation of latent variables.
    """
    with pt.defaults_scope(activation_fn=tf.nn.relu,
                           scale_after_normalization=True):
        l_z_x = (pt.wrap(x).
                 fully_connected(500).
                 batch_normalize().
                 fully_connected(500).
                 batch_normalize())
        l_z_x_mean = l_z_x.fully_connected(n_z, activation_fn=None)
        l_z_x_logstd = l_z_x.fully_connected(n_z, activation_fn=None)
    return l_z_x_mean, l_z_x_logstd


def is_loglikelihood(model, x, z_proposal, n_samples=1000):
    """
    Data log likelihood (:math:`\log p(x)`) estimates using self-normalized
    importance sampling.

    :param model: A model object that has a method logprob(z, x) to compute the
        log joint likelihood of the model.
    :param x: A Tensor of shape (batch_size, n_x). The observed variables (
        data).
    :param z_proposal: A :class:`Variational` object used as the proposal
        in importance sampling.
    :param n_samples: Int. Number of samples used in this estimate.

    :return: A Tensor of shape (batch_size,). The log likelihood of data (x).
    """
    samples = z_proposal.sample(n_samples)
    log_w = model.log_prob(samples, x) - z_proposal.logpdf(samples)
    return log_mean_exp(log_w, 1)

LogDir = "/tmp/train_logs_" + time.strftime('%Y%m%d_%H%M%S',time.localtime(time.time()))
IMAGE_PIXELS = 28

flags = tf.app.flags
flags.DEFINE_string("ps_hosts", "", "192.168.245.100:2333")
flags.DEFINE_string("worker_hosts", "", "192.168.245.153:2333")
flags.DEFINE_string("job_name", "", "worker")
flags.DEFINE_integer("task_index", 0, "Index of task within the job")
flags.DEFINE_string("data_dir", "/tmp/mnist-data",
                    "Directory for storing mnist data")
flags.DEFINE_boolean("download_only", False,
                     "Only perform downloading of data; Do not proceed to "
                     "session preparation, model definition or training")
#flags.DEFINE_integer("worker_index", 0,
#                     "Worker task index, should be >= 0. worker_index=0 is "
#                     "the master worker task the performs the variable "
#                     "initialization ")
flags.DEFINE_integer("num_workers", 1,
                     "Total number of workers (must be >= 1)")
flags.DEFINE_integer("num_parameter_servers", 1,
                     "Total number of parameter servers (must be >= 1)")
flags.DEFINE_integer("grpc_port", 2333,
                     "TensorFlow GRPC port")
flags.DEFINE_string("worker_grpc_url", "grpc://192.168.245.153:2333",
                    "Worker GRPC URL (e.g., grpc://1.2.3.4:2222, or "
                    "grpc://tf-worker0:2222)")
flags.DEFINE_boolean("sync_replicas", False,
                     "Use the sync_replicas (synchronized replicas) mode, "
                     "wherein the parameter updates from workers are aggregated "
                     "before applied to avoid stale gradients")
flags.DEFINE_integer("replicas_to_aggregate", None,
                     "Number of replicas to aggregate before parameter update"
                     "is applied (For sync_replicas mode only; default: "
                     "num_workers)")

FLAGS = flags.FLAGS

if __name__ == "__main__":
    tf.set_random_seed(1234)

    # Load MNIST
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'data', 'mnist.pkl.gz')
    x_train, t_train, x_valid, t_valid, x_test, t_test = \
        dataset.load_mnist_realval(data_path)
    x_train = np.vstack([x_train, x_valid])
    np.random.seed(1234)
    x_test = np.random.binomial(1, x_test, size=x_test.shape).astype('float32')

    # Define hyper-parameters
    n_z = 40

    # Define training/evaluation parameters
    lb_samples = 1
    ll_samples = 5000
    epoches = 3000
    batch_size = 100
    test_batch_size = 100
    iters = x_train.shape[0] // batch_size
    test_iters = x_test.shape[0] // test_batch_size
    test_freq = 10

    ps_hosts = FLAGS.ps_hosts.split(",")
    worker_hosts = FLAGS.worker_hosts.split(",")
    
    # Create a cluster from the parameter server and worker hosts.
    clusterSpec = tf.train.ClusterSpec({"ps": ps_hosts, "worker": worker_hosts})
    
    print("Create and start a server for the local task.")
    # Create and start a server for the local task.
    server = tf.train.Server(clusterSpec,
                             job_name=FLAGS.job_name,
                             task_index=FLAGS.task_index)

    print("Start ps and worker server")
    if FLAGS.job_name == "ps":
        server.join()
    elif FLAGS.job_name == "worker":
        #set distributed device
        with tf.device(tf.train.replica_device_setter(
            worker_device="/job:worker/task:%d" % FLAGS.task_index,
            cluster=clusterSpec)):
            
            # Build the training computation graph
            x = tf.placeholder(tf.float32, shape=(None, x_train.shape[1]))
            optimizer = tf.train.AdamOptimizer(learning_rate=0.001, epsilon=1e-4)
            with tf.variable_scope("model") as scope:
                with pt.defaults_scope(phase=pt.Phase.train):
                    train_model = M1(n_z, x_train.shape[1])
            with tf.variable_scope("variational") as scope:
                with pt.defaults_scope(phase=pt.Phase.train):
                    train_vz_mean, train_vz_logstd = q_net(x, n_z)
                    train_variational = ReparameterizedNormal(
                        train_vz_mean, train_vz_logstd)
            lower_bound = advi(
                train_model, x, train_variational, lb_samples)
            grads1 = optimizer.compute_gradients(-lower_bound, var_list=[
                i for i in tf.trainable_variables() if i.name.startswith('model')])
            grads2 = optimizer.compute_gradients(-lower_bound, var_list=[
                i for i in tf.trainable_variables() if i.name.startswith('variational')
            ])
            infer1 = optimizer.apply_gradients(grads1)
            infer2 = optimizer.apply_gradients(grads2)
        
            """
            # Build the evaluation computation graph
            with tf.variable_scope("model", reuse=True) as scope:
                with pt.defaults_scope(phase=pt.Phase.test):
                    eval_model = M1(n_z, x_train.shape[1])
            with tf.variable_scope("variational", reuse=True) as scope:
                with pt.defaults_scope(phase=pt.Phase.test):
                    eval_vz_mean, eval_vz_logstd = q_net(x, n_z)
                    eval_variational = ReparameterizedNormal(
                        eval_vz_mean, eval_vz_logstd)
            eval_lower_bound = is_loglikelihood(
                eval_model, x, eval_variational, lb_samples)
            eval_log_likelihood = is_loglikelihood(
                eval_model, x, eval_variational, ll_samples)
            """

            global_step = tf.Variable(0)
            saver = tf.train.Saver()
            summary_op = tf.merge_all_summaries()
            init_op = tf.initialize_all_variables()

        # Create a "supervisor", which oversees the training process.
        sv = tf.train.Supervisor(is_chief=(FLAGS.task_index == 0),
                                 logdir=LogDir,
                                 init_op=init_op,
                                 summary_op=summary_op,
                                 #summary_op=None,
                                 saver=saver,
                                 global_step=global_step,
                                 save_model_secs=600)

        params = tf.trainable_variables()
        for i in params:
            print(i.name, i.get_shape())
 
        # Run the inference
        #with tf.Session() as sess:
        epoch = 0
        with sv.managed_session(server.target) as sess:
            #sess.run(init)
            #for epoch in range(1, epoches + 1):
            while not sv.should_stop() and epoch < epoches:
                print(epoch)
                np.random.shuffle(x_train)
                lbs = []
                for t in range(iters):
                    print("t : ", t)
                    x_batch = x_train[t * batch_size:(t + 1) * batch_size]
                    x_batch = np.random.binomial(
                        n=1, p=x_batch, size=x_batch.shape).astype('float32')
                    _, lb = sess.run([infer1, lower_bound],
                                     feed_dict={x: x_batch})
                    _, lb = sess.run([infer2, lower_bound],
                                     feed_dict={x: x_batch})
                    lbs.append(lb)
                print('Epoch {}: Lower bound = {}'.format(epoch, np.mean(lbs)))
                epoch += 1
            print("exit with")
        sv.stop()
        """
                if epoch % test_freq == 0:
                    test_lbs = []
                    test_lls = []
                    for t in range(test_iters):
                        test_x_batch = x_test[
                            t * test_batch_size: (t + 1) * test_batch_size]
                        test_lb, test_ll = sess.run(
                            [eval_lower_bound, eval_log_likelihood],
                            feed_dict={x: test_x_batch}
                        )
                        test_lbs.append(test_lb)
                        test_lls.append(test_ll)
                    print('>> Test lower bound = {}'.format(np.mean(test_lbs)))
                    print('>> Test log likelihood = {}'.format(np.mean(test_lls)))
        """
