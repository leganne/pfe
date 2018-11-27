# this file contains the behaviour of an instance of the A3C agent
# including how it interacts with the environment
# and how it updates the network
###########################
# started in august 2018
# Pedro FOLETTO PIMENTA
# laboratory I3S
###########################


from AC_Network import *


# Discounting function used to calculate discounted returns.
def discount(x, gamma):
    return scipy.signal.lfilter([1], [1, -gamma], x[::-1], axis=0)[::-1]


# Copies one set of variables to another.
# Used to set worker network parameters to those of global network.
def update_target_graph(from_scope,to_scope):
    from_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, from_scope)
    to_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, to_scope)

    op_holder = []
    for from_var,to_var in zip(from_vars,to_vars):
        op_holder.append(to_var.assign(from_var))
    return op_holder


class Worker():
    def __init__(self,env,name,s_size,a_size,trainer,model_path,global_episodes):
        self.name = "worker_" + str(name)
        self.number = name
        self.model_path = model_path
        self.trainer = trainer
        self.global_episodes = global_episodes
        self.increment = self.global_episodes.assign_add(1)
        self.episode_rewards = []
        self.episode_lengths = []
        self.episode_mean_values = []
        self.summary_writer = tf.summary.FileWriter("train_"+str(self.number))

        #Create the local copy of the network and the tensorflow op to copy global paramters to local network
        self.local_AC = AC_Network(s_size,a_size,self.name,trainer)
        self.update_local_ops = update_target_graph('global',self.name)
        self.env = env

    def train(self,rollout,sess,gamma,bootstrap_value):
        rollout = np.array(rollout) # experience buffer
        observations = rollout[:,0] #states
        actions = rollout[:,1]
        rewards = rollout[:,2]
        next_observations = rollout[:,3] # following states
        #dones = rollout[:,4]
        values = rollout[:,5]
        
        # Here we take the rewards and values from the rollout, and use them to 
        # generate the advantage and discounted returns. 
        # The advantage function uses "Generalized Advantage Estimation"
        self.rewards_plus = np.asarray(rewards.tolist() + [bootstrap_value])
        discounted_rewards = discount(self.rewards_plus,gamma)[:-1]
        self.value_plus = np.asarray(values.tolist() + [bootstrap_value])
        advantages = rewards + gamma * self.value_plus[1:] - self.value_plus[:-1]
        advantages = discount(advantages,gamma)

        # Update the global network using gradients from loss
        # Generate network statistics to periodically save
        feed_dict = {self.local_AC.target_v:discounted_rewards,
            self.local_AC.inputs:np.vstack(observations),
            self.local_AC.actions:np.vstack(actions),
            self.local_AC.advantages:advantages}
        #print("DEBUG feed_dict: " + str(feed_dict))
        v_l,p_l,g_n,v_n,_ = sess.run([self.local_AC.value_loss,
            self.local_AC.policy_loss,
            self.local_AC.grad_norms,
            self.local_AC.var_norms,
            self.local_AC.apply_grads],
            feed_dict=feed_dict)
        #print("DEBUG policy: "+str(policy[0]))
        #print("DEBUG action_mean: "+str(action_mean))
        #print("DEBUG advantages: "+str(advantages))
        #print("DEBUG np.mean(advantages): "+str(np.mean(advantages)))
        #print("DEBUG policy_loss: "+str(p_l))
        #print("DEBUG actions: "+str(actions[0]))
        #print("DEBUG len(policy[0]): "+str(len(policy[0]))+"\tlen(actions[0): "+str(len(actions[0])))
        return v_l / len(rollout),p_l / len(rollout), g_n,v_n

    def work(self,max_episode_length,gamma,sess,coord,saver):
        episode_count = sess.run(self.global_episodes)
        total_steps = 0
        print ("Starting worker " + str(self.number))
        with sess.as_default(), sess.graph.as_default():
            while not coord.should_stop():
                sess.run(self.update_local_ops)
                episode_buffer = []
                episode_values = []
                episode_states = []
                episode_reward = 0.0 # sum of all rewards of the episode
                episode_step_count = 0
                done = False

                # reset environment : new episode
                s = self.env.reset()
                #print("DEBUG state: " + str(s))

                while done == False:
                    #Take an action using probabilities from policy network output
                    action,v = sess.run([self.local_AC.policy,self.local_AC.value],
                        feed_dict={self.local_AC.inputs:[s]})
                    
                    action = action[0]

                    if(done):
                        print("DEBUG done : " + str(done))

                    # do the action (interaction with environment)
                    s1, r, done = self.env.step(action)

                    if done == False:
                        episode_states.append(s1)
                    else:
                        s1 = s

                    #print("DEBUG state type : "+ str(type(s)) + " action type : " +  str(type(action)))
                    #print("DEBUG action: " +str(action) + " (" +str(type(action)) + ")")
                    action = list(action)
                        
                    episode_buffer.append([s,action,r,s1,done,v[0,0]])
                    episode_values.append(v[0,0])

                    episode_reward += r
                    s = s1
                    total_steps += 1
                    episode_step_count += 1

                    # If the episode hasn't ended, but the experience buffer is full, then we
                    # make an update step using that experience rollout.
                    if len(episode_buffer) == 30 and done == False and episode_step_count != max_episode_length - 1:
                        # Since we don't know what the true final return is,
                        # we "bootstrap" from our current value estimation.
                        v1 = sess.run(self.local_AC.value,
                            feed_dict={self.local_AC.inputs:[s]})[0,0]
                        #print("DEBUG total_steps : " + str(total_steps))
                        v_l,p_l,g_n,v_n = self.train(episode_buffer,sess,gamma,v1)
                        episode_buffer = []
                        sess.run(self.update_local_ops)
                    if done == True or episode_step_count == max_episode_length:
                        break # finishes the episode
                                            
                self.episode_rewards.append(episode_reward)
                self.episode_lengths.append(episode_step_count)
                self.episode_mean_values.append(np.mean(episode_values))
                #DEBUG prints (to monitor the learning from the terminal)
                print("****\nDEBUG episode_count: " + str(episode_count))
                print("DEBUG episode reward : " + str(episode_reward))
                print("DEBUG episode lenght : " + str(episode_step_count))
                print("DEBUG episode mean value : " + str(np.mean(episode_values)))
                
                # Update the network using the episode buffer at the end of the episode.
                if len(episode_buffer) != 0:
                    v_l,p_l,g_n,v_n = self.train(episode_buffer,sess,gamma,0.0)
                    print("DEBUG value loss : " + str(v_l))
                    print("DEBUG policy loss : " + str(p_l))

                # Periodically save model parameters and summary statistics.
                if episode_count % 5 == 0 and episode_count != 0:
                    if episode_count % 250 == 0 and self.name == 'worker_0':
                        saver.save(sess,self.model_path+'/model-'+str(episode_count)+'.cptk')
                        print ("Saved Model")

                    mean_reward = np.mean(self.episode_rewards[-5:])
                    mean_length = np.mean(self.episode_lengths[-5:])
                    mean_value = np.mean(self.episode_mean_values[-5:])
                    summary = tf.Summary()
                    summary.value.add(tag='Perf/Reward', simple_value=float(mean_reward))
                    summary.value.add(tag='Perf/Length', simple_value=float(mean_length))
                    summary.value.add(tag='Perf/Value', simple_value=float(mean_value))
                    summary.value.add(tag='Losses/Value Loss', simple_value=float(v_l))
                    summary.value.add(tag='Losses/Policy Loss', simple_value=float(p_l))
                    #summary.value.add(tag='Losses/Entropy', simple_value=float(e_l)) # we don't have this anymore
                    summary.value.add(tag='Losses/Grad Norm', simple_value=float(g_n))
                    summary.value.add(tag='Losses/Var Norm', simple_value=float(v_n))
                    self.summary_writer.add_summary(summary, episode_count)

                    self.summary_writer.flush()

                # Periodic DEBUG
                if episode_count % 100 == 0:
                    print("DEBUG episode_count : " + str(episode_count))
                    print("** mean of last 100 episode_rewards : " +str(np.mean(self.episode_rewards[-100:])))
                    print("** mean of last 100 episode_lengths : " +str(np.mean(self.episode_lengths[-100:])))
                    print("** mean of last 100 episode_mean_values : " +str(np.mean(self.episode_mean_values[-100:])))

                if self.name == 'worker_0':
                    sess.run(self.increment)
                episode_count += 1

